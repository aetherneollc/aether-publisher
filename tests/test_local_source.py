import pytest
import yaml

from dusk_publisher.errors import AssetNotFoundError, QueueFormatError
from dusk_publisher.sources.local import LocalQueueSource


def _write_queue(path, publications, languages=None):
    path.write_text(
        yaml.safe_dump({"languages": languages or {}, "publications": publications}, allow_unicode=True)
    )


@pytest.mark.asyncio
async def test_fetch_work_filters_to_approved_only(tmp_path):
    queue_file = tmp_path / "q.yaml"
    _write_queue(
        tmp_path / "q.yaml",
        [
            {
                "pub_id": "a",
                "story_id": "s",
                "episode_code": "e1",
                "lang": "zh",
                "platform": "youtube",
                "kind": "long",
                "asset_key": "a.mp4",
                "status": "approved",
            },
            {
                "pub_id": "b",
                "story_id": "s",
                "episode_code": "e1",
                "lang": "zh",
                "platform": "youtube",
                "kind": "long",
                "asset_key": "b.mp4",
                "status": "published",
            },
        ],
    )
    source = LocalQueueSource(queue_file)
    pending, langs = await source.fetch_work()
    assert [p.pub_id for p in pending] == ["a"]


@pytest.mark.asyncio
async def test_all_publications_returns_everything_regardless_of_status(tmp_path):
    queue_file = tmp_path / "q.yaml"
    _write_queue(
        queue_file,
        [
            {"pub_id": "a", "story_id": "s", "episode_code": "e1", "lang": "zh", "platform": "youtube", "kind": "long", "asset_key": "a.mp4", "status": "approved"},
            {"pub_id": "b", "story_id": "s", "episode_code": "e1", "lang": "zh", "platform": "youtube", "kind": "long", "asset_key": "b.mp4", "status": "published"},
        ],
    )
    source = LocalQueueSource(queue_file)
    all_pubs = await source.all_publications()
    assert {p.pub_id for p in all_pubs} == {"a", "b"}


@pytest.mark.asyncio
async def test_claim_resolves_relative_asset_key(tmp_path, fake_media):
    queue_file = tmp_path / "q.yaml"
    _write_queue(
        queue_file,
        [
            {
                "pub_id": "a",
                "story_id": "s",
                "episode_code": "e1",
                "lang": "zh",
                "platform": "youtube",
                "kind": "long",
                "asset_key": "demo.mp4",
                "cover_key": "demo.jpg",
                "status": "approved",
            }
        ],
    )
    source = LocalQueueSource(queue_file, media_root=fake_media["root"])
    pending, _ = await source.fetch_work()
    claimed = await source.claim(pending[0])
    assert claimed.video_path == fake_media["video"]
    assert claimed.cover_path == fake_media["cover"]


@pytest.mark.asyncio
async def test_claim_raises_when_asset_missing(tmp_path):
    queue_file = tmp_path / "q.yaml"
    _write_queue(
        queue_file,
        [
            {
                "pub_id": "a",
                "story_id": "s",
                "episode_code": "e1",
                "lang": "zh",
                "platform": "youtube",
                "kind": "long",
                "asset_key": "does_not_exist.mp4",
                "status": "approved",
            }
        ],
    )
    source = LocalQueueSource(queue_file, media_root=tmp_path / "media")
    pending, _ = await source.fetch_work()
    with pytest.raises(AssetNotFoundError):
        await source.claim(pending[0])


@pytest.mark.asyncio
async def test_report_mutates_and_persists(tmp_path):
    queue_file = tmp_path / "q.yaml"
    _write_queue(
        queue_file,
        [
            {
                "pub_id": "a",
                "story_id": "s",
                "episode_code": "e1",
                "lang": "zh",
                "platform": "youtube",
                "kind": "long",
                "asset_key": "a.mp4",
                "status": "approved",
            }
        ],
    )
    source = LocalQueueSource(queue_file)
    await source.report("a", "published", url="https://youtu.be/abc")

    all_pubs = await source.all_publications()
    assert all_pubs[0].status.value == "published"
    assert all_pubs[0].url == "https://youtu.be/abc"
    assert all_pubs[0].published_at is not None

    # Re-fetch as a fresh source instance to prove it was actually written to disk.
    source2 = LocalQueueSource(queue_file)
    pending, _ = await source2.fetch_work()
    assert pending == []  # no longer "approved"


@pytest.mark.asyncio
async def test_report_unknown_pub_id_raises(tmp_path):
    queue_file = tmp_path / "q.yaml"
    _write_queue(queue_file, [])
    source = LocalQueueSource(queue_file)
    with pytest.raises(QueueFormatError):
        await source.report("nope", "published")


@pytest.mark.asyncio
async def test_missing_queue_file_raises(tmp_path):
    source = LocalQueueSource(tmp_path / "does_not_exist.yaml")
    with pytest.raises(QueueFormatError):
        await source.fetch_work()
