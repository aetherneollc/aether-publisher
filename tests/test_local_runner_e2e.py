"""End-to-end self-test for local queue mode, using REAL adapters (no
mocking of dusk_publisher.platforms.*) against a real ffmpeg-generated fake
mp4. There are no cookie files anywhere in this test, by design: this is
exactly the "runs to the login prompt" success bar from the project brief.

What this proves works, for real, without ever touching a network or a
browser:
  - local YAML queue parsing (languages + publications)
  - account routing (platform+lang -> account name, with generic fallback)
  - asset resolution (asset_key/cover_key -> real local file paths)
  - parent_pub_id gating (a short must NOT be attempted while its parent
    long-form video is not yet `published`)
  - dropback readiness: once the parent *is* published, the short becomes
    eligible and the pipeline reaches the platform adapter (which then
    correctly reports NeedsLoginError, since no cookie exists)
  - queue-file state persistence (status/error get written back)

What it deliberately does NOT do: launch a browser, or contact any network.
`PlatformAdapter.publish()` raises `NeedsLoginError` from the cookie-file
gate before any of that happens (see platforms/base.py).
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from dusk_publisher.accounts.router import AccountEntry, AccountRouter
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.settings import Settings
from dusk_publisher.sources.local import LocalQueueSource


def _settings(data_dir) -> Settings:
    s = Settings()
    s.data_dir = data_dir
    s.headless = True
    return s


def _build_queue(tmp_path, fake_media, *, long_already_published: bool) -> LocalQueueSource:
    media_root = fake_media["root"]
    # Reuse the same fake video for the short too (content doesn't matter).
    short_video = media_root / "demo_short.mp4"
    shutil.copyfile(fake_media["video"], short_video)

    long_record = {
        "pub_id": "story1-ep01-zh-yt-long",
        "story_id": "story1",
        "episode_code": "ep01",
        "lang": "zh",
        "platform": "youtube",
        "kind": "long",
        "asset_key": "demo.mp4",
        "cover_key": "demo.jpg",
        "title": "第一集",
        "description": "正片描述",
        "tags": ["暮色工房"],
        "playlist": "第一季",
        "status": "published" if long_already_published else "approved",
    }
    if long_already_published:
        long_record["url"] = "https://youtu.be/FAKE12345"

    short_tiktok = {
        "pub_id": "story1-ep01-zh-tt-short",
        "story_id": "story1",
        "episode_code": "ep01",
        "lang": "zh",
        "platform": "tiktok",
        "kind": "short",
        "asset_key": "demo_short.mp4",
        "title": "预告",
        "description": "看正片",
        "tags": ["暮色工房"],
        "parent_pub_id": "story1-ep01-zh-yt-long",
        "status": "approved",
    }
    short_x = {
        "pub_id": "story1-ep01-zh-x-short",
        "story_id": "story1",
        "episode_code": "ep01",
        "lang": "zh",
        "platform": "x",
        "kind": "short",
        "asset_key": "demo_short.mp4",
        "title": "预告",
        "description": "",
        "tags": ["暮色工房"],
        "parent_pub_id": "story1-ep01-zh-yt-long",
        "status": "approved",
    }

    queue_file = tmp_path / "local_queue.yaml"
    queue_file.write_text(
        yaml.safe_dump(
            {
                "languages": {"zh": {"name": "简体中文", "platform_tag_style": "cn"}},
                "publications": [long_record, short_tiktok, short_x],
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return LocalQueueSource(queue_file, media_root=media_root)


def _router() -> AccountRouter:
    return AccountRouter(
        [
            AccountEntry(platform="youtube", name="yt_zh_main", lang="zh"),
            AccountEntry(platform="tiktok", name="tt_generic", lang=None),
            AccountEntry(platform="x", name="x_generic", lang=None),
        ]
    )


@pytest.mark.asyncio
async def test_no_cookies_stops_at_login_and_gates_shorts(tmp_path, data_dir, fake_media):
    """Primary self-test scenario: nothing published yet, no cookies anywhere."""
    source = _build_queue(tmp_path, fake_media, long_already_published=False)
    pipeline = PublisherPipeline(source, _router(), _settings(data_dir))

    summary = await pipeline.run_until_stable()

    # The long video reaches the adapter and correctly stops at "needs login".
    assert ("story1-ep01-zh-yt-long", "youtube", "yt_zh_main") in summary.needs_login
    assert summary.published == []
    assert summary.failed == []

    # The shorts must NOT have been attempted at all: their parent never
    # reached `published`, so they stay gated.
    assert set(summary.skipped_waiting_on_parent) == {
        "story1-ep01-zh-tt-short",
        "story1-ep01-zh-x-short",
    }

    # Queue file reflects reality: long failed with a clear "needs login" message.
    all_pubs = {p.pub_id: p for p in await source.all_publications()}
    long_pub = all_pubs["story1-ep01-zh-yt-long"]
    assert long_pub.status.value == "failed"
    assert "needs login" in long_pub.error
    assert "dusk-publisher youtube login --account yt_zh_main" in long_pub.error

    # Shorts were never touched (still `approved`, no error recorded).
    assert all_pubs["story1-ep01-zh-tt-short"].status.value == "approved"
    assert all_pubs["story1-ep01-zh-x-short"].status.value == "approved"


@pytest.mark.asyncio
async def test_shorts_unblock_once_parent_published_and_reach_login_gate(tmp_path, data_dir, fake_media):
    """Simulates the control-plane-confirmed state: the long video is already
    `published` with a real URL. The shorts should become eligible, have the
    dropback URL resolved via parent_pub_id, and reach their own platform
    adapters (again stopping at NeedsLoginError, since still no cookies)."""
    source = _build_queue(tmp_path, fake_media, long_already_published=True)
    pipeline = PublisherPipeline(source, _router(), _settings(data_dir))

    summary = await pipeline.run_until_stable()

    assert summary.skipped_waiting_on_parent == []
    assert summary.published == []
    assert summary.failed == []
    needs_login_ids = {pub_id for pub_id, _, _ in summary.needs_login}
    assert needs_login_ids == {"story1-ep01-zh-tt-short", "story1-ep01-zh-x-short"}

    all_pubs = {p.pub_id: p for p in await source.all_publications()}
    assert all_pubs["story1-ep01-zh-tt-short"].status.value == "failed"
    assert all_pubs["story1-ep01-zh-x-short"].status.value == "failed"
    assert "needs login" in all_pubs["story1-ep01-zh-tt-short"].error
