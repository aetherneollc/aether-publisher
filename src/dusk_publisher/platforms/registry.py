"""Platform name -> adapter class registry."""

from __future__ import annotations

from dusk_publisher.platforms.base import PlatformAdapter
from dusk_publisher.settings import Settings


def _adapters() -> dict[str, type[PlatformAdapter]]:
    # Imported lazily (inside the function) so `import dusk_publisher.platforms`
    # doesn't transitively require patchright unless an adapter is actually used.
    from dusk_publisher.platforms.tiktok.adapter import TikTokAdapter
    from dusk_publisher.platforms.x.adapter import XAdapter
    from dusk_publisher.platforms.youtube.adapter import YouTubeAdapter

    return {
        YouTubeAdapter.name: YouTubeAdapter,
        TikTokAdapter.name: TikTokAdapter,
        XAdapter.name: XAdapter,
    }


def list_platforms() -> list[str]:
    return list(_adapters().keys())


def get_adapter(platform: str, settings: Settings) -> PlatformAdapter:
    adapters = _adapters()
    cls = adapters.get(platform)
    if cls is None:
        raise ValueError(f"Unknown platform {platform!r}, expected one of {list(adapters)}")
    return cls(settings)
