"""Container health: the VPS-facing daemon is otherwise a black box — dead
process, browser that can't launch, full disk, and an unwritable cookie
volume all look identical from the outside ("nothing got published") unless
something actively checks and reports on them.

Three consumers of this module:
  1. `docker ps` / `docker-compose`'s own healthcheck — via the `/healthz`
     HTTP endpoint (see healthz_server.py), gated by `HealthState.ok`.
  2. A human watching the 暮色工房 control plane UI — via the `health` object
     on `/ops/heartbeat` (exact field names dictated by contract v1; see
     `HealthState.to_heartbeat_payload()`).
  3. Whoever's debugging on the VPS directly — `curl localhost:8080/healthz`
     returns a superset with a `checks` breakdown for humans, not just the
     four contract fields.

Failure-mode coverage (each one gets its own probe + a specific
`last_error` string when it's the reason `ok` is False):
  - process alive: implicit — if `/healthz` answers at all, the event loop
    is alive; if the process is dead, curl/HEALTHCHECK itself fails/times
    out and Docker marks the container unhealthy without any help from us.
  - browser can actually launch (patchright/Chromium install/deps broken):
    `probe_browser_launch()` — the single most likely thing to silently
    break on an unattended VPS (apt upgrade breaking a shared lib, a bad
    image rebuild, disk full during `patchright install`, ...).
  - disk free space: `probe_disk()`.
  - cookie volume read/write: `probe_cookie_volume()`.
  - control-plane reachability: tracked passively by `ControlPlaneSource`
    itself (`last_request_ok`/`last_request_error`) and folded in here,
    since a publisher daemon that cannot reach its control plane is
    materially not doing its job, even though the container's own
    resources are otherwise fine. This only matters in daemon/pull mode —
    local queue mode never starts this module at all.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dusk_publisher import __version__
from dusk_publisher.logging_setup import get_logger

log = get_logger(__name__)

# Priority order for which failing check gets to name `last_error` when
# several are broken at once — most actionable/severe first.
_CHECK_ORDER = ("browser", "disk", "cookie_volume", "control_plane")


@dataclass
class HealthState:
    """Mutable health snapshot, updated by pipeline/daemon.py's loop and
    read by both the /healthz HTTP handler (different thread) and the
    /ops/heartbeat payload builder. Every mutating method reassigns whole
    fields with primitive values, which is atomic enough under the GIL for
    a monitoring endpoint — no lock needed for this use case.
    """

    started_at: float = field(default_factory=time.monotonic)

    browser_ok: bool | None = None  # None = not probed yet
    browser_error: str | None = None
    disk_free_mb: int | None = None
    disk_ok: bool | None = None
    cookie_volume_ok: bool | None = None
    cookie_volume_error: str | None = None
    control_plane_ok: bool | None = None
    control_plane_error: str | None = None

    queue_depth: int = 0
    last_error: str | None = None
    last_success_at: datetime | None = None

    @property
    def uptime_s(self) -> int:
        return int(time.monotonic() - self.started_at)

    @property
    def ok(self) -> bool:
        # Treat not-yet-probed (`None`) as "not proven broken" rather than
        # failing on startup before the first probe has had a chance to run.
        return all(v is not False for v in (self.browser_ok, self.disk_ok, self.cookie_volume_ok, self.control_plane_ok))

    def compute_last_error(self) -> str | None:
        reasons = {
            "browser": (self.browser_ok is False, f"browser_launch_failed: {self.browser_error}"),
            "disk": (self.disk_ok is False, f"disk_full: only {self.disk_free_mb}MB free"),
            "cookie_volume": (self.cookie_volume_ok is False, f"cookie_volume_not_writable: {self.cookie_volume_error}"),
            "control_plane": (self.control_plane_ok is False, f"control_plane_unreachable: {self.control_plane_error}"),
        }
        for name in _CHECK_ORDER:
            broken, message = reasons[name]
            if broken:
                return message
        return None

    def record_success(self) -> None:
        self.last_success_at = datetime.now(timezone.utc)

    def record_error(self, message: str) -> None:
        self.last_error = message

    def to_heartbeat_payload(self) -> dict:
        """Exactly the `health` object shape dictated by contract v1 — no
        more, no less, per "字段名请严格按下面这份"."""
        self.last_error = self.compute_last_error()
        return {
            "ok": self.ok,
            "version": __version__,
            "uptime_s": self.uptime_s,
            "browser_ok": bool(self.browser_ok),
            "disk_free_mb": self.disk_free_mb or 0,
            "queue_depth": self.queue_depth,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
        }

    def to_debug_dict(self) -> dict:
        """Superset for humans (`curl localhost:8080/healthz`): the four
        contract fields plus a breakdown of every individual check."""
        payload = self.to_heartbeat_payload()
        payload["checks"] = {
            "browser_ok": self.browser_ok,
            "browser_error": self.browser_error,
            "disk_ok": self.disk_ok,
            "disk_free_mb": self.disk_free_mb,
            "cookie_volume_ok": self.cookie_volume_ok,
            "cookie_volume_error": self.cookie_volume_error,
            "control_plane_ok": self.control_plane_ok,
            "control_plane_error": self.control_plane_error,
        }
        return payload


async def probe_browser_launch(*, proxy: str | None = None, timeout_s: float = 30.0) -> tuple[bool, str | None]:
    """Actually try to launch a headless Chromium via patchright and close it
    again. This is the check that catches "patchright/Chromium installed
    but broken" (missing shared libs, disk full mid-install, wrong
    architecture, ...) — a plain `import patchright` or "is the process
    alive" check would miss all of that."""
    import asyncio

    try:
        from dusk_publisher.platforms.browser import close_session, new_session
    except Exception as exc:  # pragma: no cover - would mean our own package is broken
        return False, f"could not import browser helpers: {exc}"

    try:
        playwright, browser, context, page = await asyncio.wait_for(
            new_session(storage_state=None, headless=True, proxy=proxy), timeout=timeout_s
        )
    except ImportError as exc:
        return False, f"patchright not installed: {exc}"
    except Exception as exc:  # noqa: BLE001 - genuinely want to catch anything here
        return False, f"chromium launch failed: {exc}"

    try:
        await close_session(playwright, browser)
    except Exception as exc:  # noqa: BLE001 - launch already succeeded; closing failing is worth knowing but not fatal
        log.warning("browser probe: launched fine but close_session raised: %s", exc)
    return True, None


def probe_disk(path: Path, min_free_mb: int) -> tuple[bool, int]:
    try:
        usage = shutil.disk_usage(str(path))
        free_mb = usage.free // (1024 * 1024)
    except OSError as exc:
        log.warning("disk probe failed for %s: %s", path, exc)
        return False, 0
    return free_mb >= min_free_mb, free_mb


def probe_cookie_volume(accounts_dir: Path) -> tuple[bool, str | None]:
    """Write + read back + delete a small marker file in the cookie volume.
    Catches the classic "volume mounted read-only" / "permission denied
    after a `docker compose down -v` re-create" / "disk full" failure modes
    that would otherwise only surface later, confusingly, as a failed
    `login`/cookie-refresh deep inside a browser automation step."""
    marker = accounts_dir / ".dusk_publisher_health_probe"
    try:
        accounts_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()))
        marker.read_text()
        marker.unlink()
        return True, None
    except OSError as exc:
        return False, str(exc)
