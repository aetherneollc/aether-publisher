"""Pull-mode task source: talks to 暮色工房's `/ops/*` control plane API.

Contract v1 (see project README "Shared contract" section — this is the C
line's API surface, we are only a consumer):

    GET  /ops/work?agent=<id>   -> {"publications": [...], "languages": {...}}
    POST /ops/claim             {"pub_id", "agent"} -> {"ok", "asset_url", "pub"}
    POST /ops/report            {"pub_id", "status", "url", "error"}
    POST /ops/heartbeat         {"agent", "accounts": [{"platform","name","cookie_ok"}]}

Auth: header `X-Studio-Agent-Key: <STUDIO_AGENT_KEY>` on every request.
401 body shape: {"ok": false, "error": "unauthorized"}.

Contract v1 history (kept here since this module is the one place that
actually talks to `/ops/*`):
  - `/ops/claim` originally only documented a single `asset_url` (video).
    We flagged the missing cover signed-URL in the previous round; the
    control plane now also returns `cover_url` when the row has a
    `cover_key`, and `claim()` below downloads it when present.
  - A short's `parent_url` (the long-form video it promotes) used to be
    unreachable in pull mode, since `/ops/work` only returns
    `status=approved` rows and the parent is `published` by the time the
    short is released. We flagged this too; the control plane now
    denormalizes `parent_url` directly onto the short's own row (see
    `models.Publication.parent_url`), so no extra lookup is needed here.
  - `/ops/report` now accepts `status="needs_login"` (see errors.py /
    pipeline/runner.py) as distinct from `"failed"` — the control plane
    rolls the row back to `approved` for retry and flags the account.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from dusk_publisher.errors import AssetNotFoundError, ControlPlaneError
from dusk_publisher.logging_setup import get_logger
from dusk_publisher.models import LanguageMeta, Publication
from dusk_publisher.sources.base import ClaimedAsset, TaskSource

log = get_logger(__name__)


class ControlPlaneSource(TaskSource):
    def __init__(self, base_url: str, api_key: str, agent_id: str, cache_dir: Path, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            headers={"X-Studio-Agent-Key": api_key},
            timeout=timeout,
        )
        # Lightweight connectivity bookkeeping, read by health.py to decide
        # `control_plane_ok` / `last_error` for the /healthz endpoint and
        # the heartbeat's own `health` payload. Updated on every request,
        # success or failure, never raises on its own.
        self.last_request_ok: bool = True
        self.last_request_error: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            self.last_request_ok, self.last_request_error = False, f"{path}: {exc}"
            raise ControlPlaneError(path, None, str(exc)) from exc
        if resp.status_code == 401:
            self.last_request_ok, self.last_request_error = False, f"{path}: unauthorized"
            raise ControlPlaneError(path, 401, "unauthorized — check STUDIO_AGENT_KEY")
        if resp.status_code >= 400:
            self.last_request_ok, self.last_request_error = False, f"{path}: HTTP {resp.status_code}"
            raise ControlPlaneError(path, resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as exc:
            self.last_request_ok, self.last_request_error = False, f"{path}: non-JSON response"
            raise ControlPlaneError(path, resp.status_code, f"non-JSON response: {resp.text[:200]}") from exc
        self.last_request_ok, self.last_request_error = True, None
        return data

    async def fetch_work(self) -> tuple[list[Publication], dict[str, LanguageMeta]]:
        data = await self._request("GET", "/ops/work", params={"agent": self.agent_id})
        pubs = [Publication(**item) for item in data.get("publications", [])]
        langs = {k: LanguageMeta(**v) for k, v in (data.get("languages") or {}).items()}
        return pubs, langs

    async def claim(self, pub: Publication) -> ClaimedAsset:
        data = await self._request("POST", "/ops/claim", json={"pub_id": pub.pub_id, "agent": self.agent_id})
        if not data.get("ok"):
            raise ControlPlaneError("/ops/claim", None, str(data))
        asset_url = data.get("asset_url")
        if not asset_url:
            raise AssetNotFoundError(f"/ops/claim for {pub.pub_id} returned no asset_url")
        video_path = self.cache_dir / f"{pub.pub_id}{Path(pub.asset_key).suffix or '.mp4'}"
        await self._download(asset_url, video_path)

        cover_path: Path | None = None
        cover_url = data.get("cover_url")
        if cover_url:
            cover_path = self.cache_dir / f"{pub.pub_id}{Path(pub.cover_key).suffix or '.jpg'}"
            try:
                await self._download(cover_url, cover_path)
            except AssetNotFoundError as exc:
                # Cover is optional — never let a broken thumbnail block publishing
                # the video itself, same treatment local-queue mode already gives it.
                cover_path = None
                log.warning("[%s] cover download failed (non-fatal): %s", pub.pub_id, exc)
        return ClaimedAsset(video_path=video_path, cover_path=cover_path)

    async def _download(self, url: str, dest: Path) -> None:
        try:
            async with self._client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ControlPlaneError(url, resp.status_code, await resp.aread())
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as exc:
            raise AssetNotFoundError(f"failed downloading asset from signed URL: {exc}") from exc

    async def report(
        self,
        pub_id: str,
        status: str,
        url: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._request(
            "POST",
            "/ops/report",
            json={"pub_id": pub_id, "status": status, "url": url, "error": error},
        )

    async def heartbeat(self, accounts: list[dict], health: dict | None = None) -> None:
        payload: dict = {"agent": self.agent_id, "accounts": accounts}
        if health is not None:
            payload["health"] = health
        await self._request("POST", "/ops/heartbeat", json=payload)
