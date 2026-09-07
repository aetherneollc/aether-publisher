"""TaskSource: the abstraction that lets the same pipeline run against either
a local YAML/JSON queue file or the 暮色工房 control plane's /ops/* API.

Both concrete sources (`LocalQueueSource`, `ControlPlaneSource`) resolve
whatever they're given down to a *local file path* in `ClaimedAsset` — the
publish pipeline and every platform adapter never know or care whether that
path came from disk directly or was just downloaded from a signed R2 URL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from dusk_publisher.models import LanguageMeta, Publication


class ClaimedAsset(BaseModel):
    video_path: Path
    cover_path: Path | None = None


class TaskSource(ABC):
    """Anything that can hand out `Publication` work items and take reports back."""

    @abstractmethod
    async def fetch_work(self) -> tuple[list[Publication], dict[str, LanguageMeta]]:
        """Return currently-approved publications plus the language registry."""

    @abstractmethod
    async def claim(self, pub: Publication) -> ClaimedAsset:
        """Resolve/download the media for `pub`, returning local paths."""

    @abstractmethod
    async def report(
        self,
        pub_id: str,
        status: str,
        url: str | None = None,
        error: str | None = None,
    ) -> None:
        """Report a publish outcome back to the source of truth."""

    async def heartbeat(self, accounts: list[dict], health: dict | None = None) -> None:
        """Report cookie-health for a batch of accounts, plus optional
        container-level `health` telemetry (see health.py). No-op by
        default (only meaningful for the control plane, which surfaces
        this to a human as "XX account needs re-login" / a liveness
        indicator)."""
        return None

    async def aclose(self) -> None:
        """Release any resources (HTTP clients, etc). No-op by default."""
        return None
