from __future__ import annotations

import pytest

from dusk_publisher.accounts.router import AccountEntry, AccountRouter
from dusk_publisher.models import LanguageMeta, Publication
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.settings import Settings
from dusk_publisher.sources.base import ClaimedAsset, TaskSource


class MemorySource(TaskSource):
    def __init__(self, publications, tmp_path):
        self.publications = publications
        self.tmp_path = tmp_path
        self.claimed = []
        self.reported = []

    async def fetch_work(self):
        return self.publications, {"en": LanguageMeta(name="English")}

    async def claim(self, pub):
        self.claimed.append(pub.pub_id)
        return ClaimedAsset(video_path=self.tmp_path / f"{pub.pub_id}.mp4")

    async def report(self, pub_id, status, url=None, error=None):
        self.reported.append((pub_id, status))


def make_pub(pub_id: str, platform: str) -> Publication:
    return Publication(
        pub_id=pub_id,
        story_id="story",
        episode_code=pub_id,
        lang="en",
        platform=platform,
        kind="long" if platform == "youtube" else "short",
        asset_key=f"{pub_id}.mp4",
    )


@pytest.mark.asyncio
async def test_batch_filters_platform_and_limits_selection(tmp_path, monkeypatch):
    source = MemorySource(
        [make_pub("x-1", "x"), make_pub("yt-2", "youtube"), make_pub("yt-1", "youtube")],
        tmp_path,
    )
    router = AccountRouter(
        [
            AccountEntry(platform="youtube", name="yt", lang=None),
            AccountEntry(platform="x", name="x", lang=None),
        ]
    )
    settings = Settings()
    settings.data_dir = tmp_path
    pipeline = PublisherPipeline(source, router, settings)

    summary = await pipeline.run_once(max_tasks=1, platforms=["youtube"], group_by_account=True)

    assert summary.pending_count == 3
    assert summary.selected_count == 1
    assert source.claimed == ["yt-1"]
