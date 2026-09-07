"""Verifies the dropback URL is actually threaded into the PublishRequest
handed to the short's adapter, by substituting a recording stub adapter for
`get_adapter` (the only thing monkeypatched in this file — routing, queue
parsing, gating, and asset resolution are all still the real thing).
"""

from __future__ import annotations

import pytest
import yaml

from dusk_publisher.accounts.router import AccountEntry, AccountRouter
from dusk_publisher.models import CheckResult, LoginResult, PublishResult
from dusk_publisher.pipeline import runner as runner_module
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.platforms.base import PlatformAdapter
from dusk_publisher.settings import Settings
from dusk_publisher.sources.local import LocalQueueSource


class RecordingAdapter(PlatformAdapter):
    name = "tiktok"
    received_requests: list = []

    async def login(self, account):  # pragma: no cover - unused here
        return LoginResult(ok=True)

    async def check(self, account):  # pragma: no cover - unused here
        return CheckResult(ok=True)

    async def publish(self, account, req):
        RecordingAdapter.received_requests.append(req)
        return PublishResult(ok=True, url="https://tiktok.com/@x/video/999")

    async def _publish_impl(self, account, req):  # pragma: no cover - publish() is overridden above
        return await self.publish(account, req)


@pytest.mark.asyncio
async def test_short_publish_request_contains_injected_parent_url(tmp_path, monkeypatch, fake_media):
    RecordingAdapter.received_requests = []
    monkeypatch.setattr(runner_module, "get_adapter", lambda platform, settings: RecordingAdapter(settings))

    queue_file = tmp_path / "q.yaml"
    queue_file.write_text(
        yaml.safe_dump(
            {
                "languages": {"zh": {"name": "简体中文"}},
                "publications": [
                    {
                        "pub_id": "long1",
                        "story_id": "s",
                        "episode_code": "e1",
                        "lang": "zh",
                        "platform": "youtube",
                        "kind": "long",
                        "asset_key": "demo.mp4",
                        "status": "published",
                        "url": "https://youtu.be/PARENT123",
                    },
                    {
                        "pub_id": "short1",
                        "story_id": "s",
                        "episode_code": "e1",
                        "lang": "zh",
                        "platform": "tiktok",
                        "kind": "short",
                        "asset_key": "demo.mp4",
                        "description": "预告片",
                        "parent_pub_id": "long1",
                        "status": "approved",
                    },
                ],
            },
            allow_unicode=True,
        )
    )
    source = LocalQueueSource(queue_file, media_root=fake_media["root"])
    router = AccountRouter([AccountEntry(platform="tiktok", name="tt_generic", lang=None)])
    settings = Settings()
    pipeline = PublisherPipeline(source, router, settings)

    summary = await pipeline.run_once()

    assert summary.published == [("short1", "https://tiktok.com/@x/video/999")]
    assert len(RecordingAdapter.received_requests) == 1
    req = RecordingAdapter.received_requests[0]
    assert req.parent_url == "https://youtu.be/PARENT123"
    assert "https://youtu.be/PARENT123" in req.description
    assert "预告片" in req.description  # original description preserved, not replaced
