"""Tests for the contract v1 fixes adopted this round:
  - `/ops/claim` response's new `cover_url` gets downloaded when present.
  - `/ops/heartbeat` accepts an optional `health` object.
  - `last_request_ok`/`last_request_error` bookkeeping (used by health.py
    for `control_plane_ok`) reflects real success/failure through the
    actual `_request()` code path (via httpx.MockTransport — a real HTTP
    round-trip through httpx, just without touching the network).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dusk_publisher.errors import ControlPlaneError
from dusk_publisher.models import Publication
from dusk_publisher.sources.control_plane import ControlPlaneSource


def _pub(**overrides):
    data = dict(
        pub_id="p1",
        story_id="s",
        episode_code="e1",
        lang="zh",
        platform="youtube",
        kind="long",
        asset_key="video.mp4",
        cover_key="video.jpg",
    )
    data.update(overrides)
    return Publication(**data)


@pytest.mark.asyncio
async def test_claim_downloads_cover_when_cover_url_present(tmp_path, monkeypatch):
    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="a1", cache_dir=tmp_path)
    downloads = []

    async def fake_request(method, path, **kwargs):
        return {"ok": True, "asset_url": "https://cdn/video.bin", "cover_url": "https://cdn/cover.bin", "pub": {}}

    async def fake_download(url, dest):
        downloads.append(url)
        Path(dest).write_bytes(b"data")

    monkeypatch.setattr(source, "_request", fake_request)
    monkeypatch.setattr(source, "_download", fake_download)

    claimed = await source.claim(_pub())

    assert claimed.video_path.exists()
    assert claimed.cover_path is not None and claimed.cover_path.exists()
    assert downloads == ["https://cdn/video.bin", "https://cdn/cover.bin"]
    await source.aclose()


@pytest.mark.asyncio
async def test_claim_without_cover_url_leaves_cover_none(tmp_path, monkeypatch):
    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="a1", cache_dir=tmp_path)

    async def fake_request(method, path, **kwargs):
        return {"ok": True, "asset_url": "https://cdn/video.bin", "pub": {}}

    async def fake_download(url, dest):
        Path(dest).write_bytes(b"data")

    monkeypatch.setattr(source, "_request", fake_request)
    monkeypatch.setattr(source, "_download", fake_download)

    claimed = await source.claim(_pub())
    assert claimed.cover_path is None
    await source.aclose()


@pytest.mark.asyncio
async def test_claim_cover_download_failure_is_non_fatal(tmp_path, monkeypatch):
    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="a1", cache_dir=tmp_path)

    async def fake_request(method, path, **kwargs):
        return {"ok": True, "asset_url": "https://cdn/video.bin", "cover_url": "https://cdn/cover.bin", "pub": {}}

    from dusk_publisher.errors import AssetNotFoundError

    async def fake_download(url, dest):
        if "cover" in url:
            raise AssetNotFoundError("cover 404")
        Path(dest).write_bytes(b"data")

    monkeypatch.setattr(source, "_request", fake_request)
    monkeypatch.setattr(source, "_download", fake_download)

    claimed = await source.claim(_pub())
    assert claimed.video_path.exists()
    assert claimed.cover_path is None  # non-fatal, video still claimed
    await source.aclose()


@pytest.mark.asyncio
async def test_heartbeat_forwards_health_payload(tmp_path, monkeypatch):
    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="agent-1", cache_dir=tmp_path)
    seen = {}

    async def fake_request(method, path, **kwargs):
        seen["method"], seen["path"], seen["json"] = method, path, kwargs.get("json")
        return {"ok": True}

    monkeypatch.setattr(source, "_request", fake_request)

    health = {"ok": True, "version": "0.1.0", "uptime_s": 10, "browser_ok": True, "disk_free_mb": 100, "queue_depth": 0, "last_error": None, "last_success_at": None}
    await source.heartbeat([{"platform": "tiktok", "name": "a", "cookie_ok": True}], health=health)

    assert seen["path"] == "/ops/heartbeat"
    assert seen["json"]["agent"] == "agent-1"
    assert seen["json"]["health"] == health
    await source.aclose()


@pytest.mark.asyncio
async def test_heartbeat_omits_health_key_when_not_given(tmp_path, monkeypatch):
    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="agent-1", cache_dir=tmp_path)
    seen = {}

    async def fake_request(method, path, **kwargs):
        seen["json"] = kwargs.get("json")
        return {"ok": True}

    monkeypatch.setattr(source, "_request", fake_request)
    await source.heartbeat([])
    assert "health" not in seen["json"]
    await source.aclose()


@pytest.mark.asyncio
async def test_request_tracks_success_via_real_httpx_roundtrip(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ok": True, "publications": []})

    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="a1", cache_dir=tmp_path)
    await source._client.aclose()
    source._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    data = await source._request("GET", "/ops/work")
    assert data["ok"] is True
    assert source.last_request_ok is True
    assert source.last_request_error is None
    await source.aclose()


@pytest.mark.asyncio
async def test_request_tracks_401_failure_via_real_httpx_roundtrip(tmp_path):
    def handler(request):
        return httpx.Response(401, json={"ok": False, "error": "unauthorized"})

    source = ControlPlaneSource(base_url="https://cp.example", api_key="k", agent_id="a1", cache_dir=tmp_path)
    await source._client.aclose()
    source._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ControlPlaneError):
        await source._request("GET", "/ops/work")
    assert source.last_request_ok is False
    assert "unauthorized" in source.last_request_error
    await source.aclose()


@pytest.mark.asyncio
async def test_cloudflare_access_headers_are_added(tmp_path):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    source = ControlPlaneSource(
        base_url="https://cp.example",
        api_key="agent-key",
        agent_id="a1",
        cache_dir=tmp_path,
        cf_access_client_id="client-id.access",
        cf_access_client_secret="client-secret",
    )
    await source._client.aclose()
    source._client = httpx.AsyncClient(
        headers=source._client.headers,
        transport=httpx.MockTransport(handler),
    )

    await source._request("GET", "/ops/work")
    assert seen["x-studio-agent-key"] == "agent-key"
    assert seen["cf-access-client-id"] == "client-id.access"
    assert seen["cf-access-client-secret"] == "client-secret"
    await source.aclose()
