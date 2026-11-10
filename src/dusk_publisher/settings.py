"""Runtime settings, loaded from environment variables (+ optional .env).

Kept deliberately simple (no pydantic-settings dependency) so this file is
easy to read top to bottom. All env vars are documented in `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from dusk_publisher.browser_identity import (
    chrome_source_user_data,
    firefox_source_profile,
    resolve_browser_identity,
    resolve_daily_family,
)

# Load .env if present (repo root or CWD). Never overrides already-set env
# vars, so real container/CI env vars always win over a stray .env file.
load_dotenv(override=False)


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Control plane (pull mode)
    control_plane_url: str = field(
        default_factory=lambda: os.environ.get(
            "CONTROL_PLANE_URL", "https://anime-factory.hero-30b.workers.dev"
        )
    )
    studio_agent_key: str = field(default_factory=lambda: os.environ.get("STUDIO_AGENT_KEY", ""))
    agent_id: str = field(default_factory=lambda: os.environ.get("AGENT_ID", "dusk-publisher-01"))
    cf_access_client_id: str | None = field(
        default_factory=lambda: os.environ.get("CF_ACCESS_CLIENT_ID") or None
    )
    cf_access_client_secret: str | None = field(
        default_factory=lambda: os.environ.get("CF_ACCESS_CLIENT_SECRET") or None
    )
    account_vault_url: str | None = field(
        default_factory=lambda: os.environ.get("AETHER_VAULT_URL") or None
    )
    account_vault_runner_token: str | None = field(
        default_factory=lambda: os.environ.get("AETHER_VAULT_RUNNER_TOKEN") or None
    )

    # Filesystem layout
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "/data")))
    accounts_file: Path = field(
        default_factory=lambda: Path(os.environ.get("ACCOUNTS_FILE", "config/accounts.yaml"))
    )
    local_queue_file: Path = field(
        default_factory=lambda: Path(os.environ.get("LOCAL_QUEUE_FILE", "queue/local_queue.yaml"))
    )

    # Browser behavior.
    # auto: this desktop's installed Firefox when a profile exists, otherwise
    # Chrome. CI, Docker, and pytest stay on a fresh cookie-file context.
    # actions: GitHub Actions launches Firefox on a virtual screen. Cookie
    # files still gate publish.
    headless: bool = field(default_factory=lambda: _bool("HEADLESS", True))
    browser_identity: str = field(default_factory=lambda: os.environ.get("BROWSER_IDENTITY", "auto"))
    browser_family: str = field(default_factory=lambda: os.environ.get("BROWSER_FAMILY", "auto"))
    browser_cdp_url: str | None = field(default_factory=lambda: os.environ.get("BROWSER_CDP_URL") or None)
    # Playwright Firefox websocket. Still supported when set in the process.
    # The GitHub Actions publisher workflow does not set it and does not call the VPS.
    firefox_remote_url: str | None = field(
        default_factory=lambda: os.environ.get("FIREFOX_REMOTE_URL") or None
    )
    chrome_profile_directory: str | None = field(
        default_factory=lambda: os.environ.get("CHROME_PROFILE_DIRECTORY") or None
    )

    # Daemon timing
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    )
    check_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("CHECK_INTERVAL_SECONDS", "1800"))
    )
    # Heartbeat must fire on its own fixed cadence, independent of whether
    # there's work — that's what lets a human tell "alive but idle" apart
    # from "dead" from the control plane's side (see health.py / daemon.py).
    heartbeat_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60"))
    )
    # The browser-launch probe is the expensive health check (it actually
    # spawns a Chromium process) — run it far less often than the heartbeat
    # itself; the heartbeat just reports the last probe's cached result.
    health_check_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("HEALTH_CHECK_INTERVAL_SECONDS", "300"))
    )
    # Below this, disk_ok flips false and the container reports unhealthy —
    # uploads/downloads of video files can eat disk fast on an unattended VPS.
    disk_min_free_mb: int = field(
        default_factory=lambda: int(os.environ.get("DISK_MIN_FREE_MB", "1024"))
    )
    # Port the in-process /healthz HTTP server listens on (daemon mode only).
    healthz_port: int = field(default_factory=lambda: int(os.environ.get("HEALTHZ_PORT", "8080")))

    # Optional per-platform proxies for the ephemeral browser only.
    # Daily Firefox ignores them and uses the host default route.
    # Actions Firefox ignores them and uses the account proxy sidecar.
    # The Actions workflows do not set YT_PROXY, X_PROXY, or TIKTOK_PROXY.
    yt_proxy: str | None = field(default_factory=lambda: os.environ.get("YT_PROXY") or None)
    x_proxy: str | None = field(default_factory=lambda: os.environ.get("X_PROXY") or None)
    tiktok_proxy: str | None = field(default_factory=lambda: os.environ.get("TIKTOK_PROXY") or None)

    @property
    def cache_dir(self) -> Path:
        d = self.data_dir / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def browser_mode(self) -> str:
        # A test run must not open the desktop profile, even if .env asks for it.
        # actions is the Actions-machine Firefox, not that profile, so CI must
        # not rewrite BROWSER_IDENTITY=actions into ephemeral Chromium.
        if os.environ.get("PYTEST_CURRENT_TEST") and (self.browser_identity or "").strip().lower() != "actions":
            return "ephemeral"
        firefox = firefox_source_profile()
        chrome = chrome_source_user_data()
        return resolve_browser_identity(
            self.browser_identity,
            cdp_url=self.browser_cdp_url,
            ci=bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")),
            docker=Path("/.dockerenv").exists(),
            pytest_running=bool(os.environ.get("PYTEST_CURRENT_TEST")),
            firefox_profile_exists=bool(firefox and firefox.is_dir()),
            chrome_profile_exists=bool(chrome and chrome.is_dir()),
            firefox_remote_url=self.firefox_remote_url,
        )

    def daily_family(self) -> str:
        firefox = firefox_source_profile()
        chrome = chrome_source_user_data()
        return resolve_daily_family(
            self.browser_family,
            firefox_profile_exists=bool(firefox and firefox.is_dir()),
            chrome_profile_exists=bool(chrome and chrome.is_dir()),
        )

    def uses_real_browser(self) -> bool:
        """Daily profile or an already-open browser, instead of a cookie file.

        ``actions`` is not included. A missing storage_state still raises
        NeedsLoginError before Firefox starts.
        """
        return self.browser_mode() in {"daily", "cdp", "remote-firefox"}

    @property
    def accounts_dir(self) -> Path:
        d = self.data_dir / "accounts"
        d.mkdir(parents=True, exist_ok=True)
        return d


def get_settings() -> Settings:
    # Re-read every call (cheap) so tests can monkeypatch env vars freely
    # without needing to reset a cached singleton.
    return Settings()
