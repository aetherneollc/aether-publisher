"""Tests the contract v1 fix for dropback in pull mode: since a
control-plane-shaped `TaskSource` cannot look up arbitrary rows by id (no
`all_publications()`), the runner must use the denormalized
`Publication.parent_url` field directly instead. Uses a minimal fake
`TaskSource` that intentionally has no `all_publications` — same shape as
`ControlPlaneSource` — rather than `LocalQueueSource`, which *does* have
that method and takes the other code path (covered by
test_local_runner_e2e.py / test_dropback_injection.py already).
"""

from __future__ import annotations

import pytest

from dusk_publisher.accounts.router import AccountEntry, AccountRouter
from dusk_publisher.models import CheckResult, LoginResult, Publication, PublishResult
from dusk_publisher.pipeline import runner as runner_module
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.platforms.base import PlatformAdapter
from dusk_publisher.settings import Settings
from dusk_publisher.sources.base import ClaimedAsset, TaskSource


class FakeControlPlaneShapedSource(TaskSource):
    """Deliberately has no `all_publications()` — mirrors ControlPlaneSource."""

    def __init__(self, pubs: list[Publication], video_path):
        self._pubs = pubs
        self._video_path = video_path
        self.reports: list[tuple] = []

    async def fetch_work(self):
        pending = [p for p in self._pubs if p.is_ready_to_run()]
        return pending, {}

    async def claim(self, pub):
        return ClaimedAsset(video_path=self._video_path, cover_path=None)

    async def report(self, pub_id, status, url=None, error=None):
        self.reports.append((pub_id, status, url, error))


class RecordingAdapter(PlatformAdapter):
    name = "tiktok"
    received: list = []

    async def login(self, account):  # pragma: no cover - unused
        return LoginResult(ok=True)

    async def check(self, account):  # pragma: no cover - unused
        return CheckResult(ok=True)

    async def publish(self, account, req):
        RecordingAdapter.received.append(req)
        return PublishResult(ok=True, url="https://tiktok.com/@x/video/1")

    async def _publish_impl(self, account, req):  # pragma: no cover - publish() overridden above
        return await self.publish(account, req)


def _router() -> AccountRouter:
    return AccountRouter([AccountEntry(platform="tiktok", name="tt_generic", lang=None)])


@pytest.mark.asyncio
async def test_uses_denormalized_parent_url_when_no_lookup_available(monkeypatch, fake_media):
    RecordingAdapter.received = []
    monkeypatch.setattr(runner_module, "get_adapter", lambda platform, settings: RecordingAdapter(settings))

    short = Publication(
        pub_id="short1",
        story_id="s",
        episode_code="e1",
        lang="zh",
        platform="tiktok",
        kind="short",
        asset_key="demo.mp4",
        description="预告",
        parent_pub_id="long1",
        parent_url="https://youtu.be/PARENT999",
        status="approved",
    )
    source = FakeControlPlaneShapedSource([short], fake_media["video"])
    assert not hasattr(source, "all_publications")

    pipeline = PublisherPipeline(source, _router(), Settings())
    summary = await pipeline.run_once()

    assert summary.skipped_waiting_on_parent == []
    assert summary.published == [("short1", "https://tiktok.com/@x/video/1")]
    assert len(RecordingAdapter.received) == 1
    req = RecordingAdapter.received[0]
    assert req.parent_url == "https://youtu.be/PARENT999"
    assert "https://youtu.be/PARENT999" in req.description
    assert "预告" in req.description


@pytest.mark.asyncio
async def test_publishes_without_link_and_warns_when_parent_url_missing(monkeypatch, fake_media, caplog):
    RecordingAdapter.received = []
    monkeypatch.setattr(runner_module, "get_adapter", lambda platform, settings: RecordingAdapter(settings))

    short = Publication(
        pub_id="short2",
        story_id="s",
        episode_code="e1",
        lang="zh",
        platform="tiktok",
        kind="short",
        asset_key="demo.mp4",
        parent_pub_id="long1",
        # No parent_url set — simulates a control plane that hasn't (or
        # can't) populate it for some row; the defensive fallback in
        # runner.py should still publish, just without an injected link.
        status="approved",
    )
    source = FakeControlPlaneShapedSource([short], fake_media["video"])

    pipeline = PublisherPipeline(source, _router(), Settings())
    summary = await pipeline.run_once()

    assert summary.published == [("short2", "https://tiktok.com/@x/video/1")]
    req = RecordingAdapter.received[0]
    assert req.parent_url is None
