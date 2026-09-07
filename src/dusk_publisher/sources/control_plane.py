"""Pull-mode task source: talks to 暮色工房's `/ops/*` control plane API.

Contract v1 (see project README "Shared contract" section — this is the C
line's API surface, we are only a consumer):

    GET  /ops/work?agent=<id>   -> {"publications": [...], "languages": {...}}
    POST /ops/claim             {"pub_id", "agent"} -> {"ok", "asset_url", "pub"}
    POST /ops/report            {"pub_id", "status", "url", "error"}
    POST /ops/heartbeat         {"agent", "accounts": [{"platform","name","cookie_ok"}]}

Auth: header `X-Studio-Agent-Key: <STUDIO_AGENT_KEY>` on every request.
401 body shape: {"ok": false, "error": "unauthorized"}.

NOTE (flagged for the final report, not fixed unilaterally per instructions):
the contract's `/ops/claim` response only documents a single `asset_url` for
the video asset. There is no documented way to fetch a signed URL for
`cover_key`. This client therefore does NOT attempt to download covers in
pull-mode; `claim()` returns `cover_path=None` and adapters proceed without
a thumbnail (non-fatal, matches how the YouTube adapter already treats a
missing thumbnail). Local queue mode is unaffected since it reads the cover
straight off disk.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from dusk_publisher.errors import AssetNotFoundError, ControlPlaneError
from dusk_publisher.models import LanguageMeta, Publication
from dusk_publisher.sources.base import ClaimedAsset, TaskSource


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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ControlPlaneError(path, None, str(exc)) from exc
        if resp.status_code == 401:
            raise ControlPlaneError(path, 401, "unauthorized — check STUDIO_AGENT_KEY")
        if resp.status_code >= 400:
            raise ControlPlaneError(path, resp.status_code, resp.text)
        try:
            return resp.json()
        except ValueError as exc:
            raise ControlPlaneError(path, resp.status_code, f"non-JSON response: {resp.text[:200]}") from exc

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
        # cover_key has no documented signed-URL path yet — see module docstring.
        return ClaimedAsset(video_path=video_path, cover_path=None)

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

    async def heartbeat(self, accounts: list[dict]) -> None:
        await self._request(
            "POST",
            "/ops/heartbeat",
            json={"agent": self.agent_id, "accounts": accounts},
        )
