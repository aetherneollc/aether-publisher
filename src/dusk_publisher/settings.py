"""Runtime settings, loaded from environment variables (+ optional .env).

Kept deliberately simple (no pydantic-settings dependency) so this file is
easy to read top to bottom. All env vars are documented in `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

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

    # Filesystem layout
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "/data")))
    accounts_file: Path = field(
        default_factory=lambda: Path(os.environ.get("ACCOUNTS_FILE", "config/accounts.yaml"))
    )
    local_queue_file: Path = field(
        default_factory=lambda: Path(os.environ.get("LOCAL_QUEUE_FILE", "queue/local_queue.yaml"))
    )

    # Browser behavior
    headless: bool = field(default_factory=lambda: _bool("HEADLESS", True))

    # Daemon timing
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    )
    check_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("CHECK_INTERVAL_SECONDS", "1800"))
    )

    # Optional per-platform proxies (patchright's Chromium ignores the
    # system proxy; it must be passed explicitly if the platform is blocked
    # or degraded from the VPS's region).
    yt_proxy: str | None = field(default_factory=lambda: os.environ.get("YT_PROXY") or None)
    x_proxy: str | None = field(default_factory=lambda: os.environ.get("X_PROXY") or None)
    tiktok_proxy: str | None = field(default_factory=lambda: os.environ.get("TIKTOK_PROXY") or None)

    @property
    def cache_dir(self) -> Path:
        d = self.data_dir / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def accounts_dir(self) -> Path:
        d = self.data_dir / "accounts"
        d.mkdir(parents=True, exist_ok=True)
        return d


def get_settings() -> Settings:
    # Re-read every call (cheap) so tests can monkeypatch env vars freely
    # without needing to reset a cached singleton.
    return Settings()
