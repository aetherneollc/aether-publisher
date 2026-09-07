"""Pull-mode daemon: poll the control plane, publish, report, heartbeat —
and now, continuously self-monitor and report that too.

Three independent cadences, deliberately decoupled so an expensive check
never blocks a cheap one:
  - `poll_interval_seconds` (default 60s): `/ops/work` -> claim -> publish
    -> `/ops/report`. Also refreshes `queue_depth` for health telemetry.
  - `heartbeat_interval_seconds` (default 60s): `/ops/heartbeat`, **fires
    on its own fixed schedule regardless of whether there was any work** —
    this is what lets a human watching the control plane tell "container
    alive but idle" apart from "container dead" (previously indistinguishable:
    both looked like "nothing happened"). Uses the *cached* results of the
    two probes below, not a fresh check every time.
  - `check_interval_seconds` (default 1800s): the expensive, real
    per-account cookie `check()` (launches a browser per account) — also
    doubles as an implicit browser-launch health probe when there are
    configured accounts.
  - `health_check_interval_seconds` (default 300s): the dedicated
    browser-launch probe (`health.probe_browser_launch`) plus the cheap
    disk/cookie-volume probes, refreshed independently of whether there are
    any accounts configured at all (a freshly deployed box with zero
    accounts should still be able to say "yes, Chromium launches fine
    here").

Any exception from a single pass (network blip, control-plane hiccup) is
caught, logged, and recorded into `HealthState.last_error` — a single bad
poll should never kill the daemon, and should always be visible externally.
"""

from __future__ import annotations

import asyncio
import time

from dusk_publisher.accounts.router import AccountRouter
from dusk_publisher.health import HealthState, probe_browser_launch, probe_cookie_volume, probe_disk
from dusk_publisher.healthz_server import start_healthz_server
from dusk_publisher.logging_setup import get_logger
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.platforms.registry import get_adapter
from dusk_publisher.settings import Settings
from dusk_publisher.sources.control_plane import ControlPlaneSource

log = get_logger(__name__)


async def _cookie_check_sweep(router: AccountRouter, settings: Settings) -> list[dict]:
    """Real, per-account cookie validity checks (launches a browser per
    account). Expensive — this is why it runs on its own, longer,
    `check_interval_seconds` cadence rather than every heartbeat."""
    accounts_payload = []
    for entry in router.all():
        adapter = get_adapter(entry.platform, settings)
        result = await adapter.check(entry.name)
        accounts_payload.append({"platform": entry.platform, "name": entry.name, "cookie_ok": result.ok})
        if not result.ok:
            log.warning("[%s:%s] cookie check failed: %s", entry.platform, entry.name, result.detail)
    return accounts_payload


async def _refresh_health_probes(health: HealthState, settings: Settings) -> None:
    """The dedicated, account-independent health probes: can a browser
    launch at all, is there disk space, is the cookie volume writable."""
    browser_ok, browser_error = await probe_browser_launch(proxy=settings.yt_proxy)
    health.browser_ok, health.browser_error = browser_ok, browser_error

    disk_ok, disk_free_mb = probe_disk(settings.data_dir, settings.disk_min_free_mb)
    health.disk_ok, health.disk_free_mb = disk_ok, disk_free_mb

    cookie_ok, cookie_error = probe_cookie_volume(settings.accounts_dir)
    health.cookie_volume_ok, health.cookie_volume_error = cookie_ok, cookie_error

    log.info(
        "health probe: browser_ok=%s disk_free_mb=%s cookie_volume_ok=%s",
        browser_ok, disk_free_mb, cookie_ok,
    )


async def run_forever(settings: Settings) -> None:
    router = AccountRouter.from_file(settings.accounts_file)
    source = ControlPlaneSource(
        base_url=settings.control_plane_url,
        api_key=settings.studio_agent_key,
        agent_id=settings.agent_id,
        cache_dir=settings.cache_dir,
    )
    pipeline = PublisherPipeline(source, router, settings)

    health = HealthState()
    accounts_cache: list[dict] = [
        {"platform": e.platform, "name": e.name, "cookie_ok": True} for e in router.all()
    ]  # optimistic until the first real check_interval_seconds sweep runs

    stop_healthz = start_healthz_server(settings.healthz_port, lambda: health)

    # Run the account-independent probes once immediately at startup, so
    # `/healthz` doesn't report "unknown" for several minutes right after a
    # fresh deploy/restart.
    try:
        await _refresh_health_probes(health, settings)
    except Exception:
        log.exception("initial health probe failed")

    last_check = 0.0
    last_heartbeat = 0.0
    last_health_probe = time.monotonic()
    try:
        while True:
            try:
                summary = await pipeline.run_once()
                health.queue_depth = summary.pending_count
                if summary.made_progress:
                    health.record_success()
                if summary.failed:
                    health.record_error(f"publish failed: {summary.failed[-1][1]}")
                log.info("poll complete: %s", summary)
            except Exception as exc:
                log.exception("poll pass failed, will retry after backoff")
                health.record_error(f"poll pass failed: {exc}")

            now = time.monotonic()

            if now - last_health_probe >= settings.health_check_interval_seconds:
                try:
                    await _refresh_health_probes(health, settings)
                except Exception:
                    log.exception("health probe sweep failed")
                last_health_probe = now

            if now - last_check >= settings.check_interval_seconds and router.all():
                try:
                    accounts_cache = await _cookie_check_sweep(router, settings)
                except Exception:
                    log.exception("cookie check sweep failed, will retry next cycle")
                last_check = now

            # Reflect the most recent control-plane request's outcome
            # (updated passively by ControlPlaneSource on every call this
            # loop already makes) into health, before it's reported below.
            health.control_plane_ok = source.last_request_ok
            health.control_plane_error = source.last_request_error

            if now - last_heartbeat >= settings.heartbeat_interval_seconds:
                # Heartbeat fires on its own schedule regardless of whether
                # there was any work this cycle — see module docstring.
                try:
                    await source.heartbeat(accounts_cache, health=health.to_heartbeat_payload())
                except Exception:
                    log.exception("heartbeat failed, will retry next cycle")
                last_heartbeat = now

            await asyncio.sleep(min(settings.poll_interval_seconds, settings.heartbeat_interval_seconds))
    finally:
        stop_healthz()
        await source.aclose()
