"""Local queue mode: a standalone task source backed by a YAML/JSON file.

This is what makes dusk-publisher useful *without* 暮色工房 at all: point it
at a queue file describing publications (same field shape as the shared
contract) plus a `media_root` for resolving `asset_key`/`cover_key` as local
paths, and it behaves exactly like the control-plane pull mode from the
pipeline's point of view.

The queue file is both the input and the state store: `report()` mutates the
matching record's status/url/error/published_at in place and rewrites the
file, so re-running `queue run` is idempotent and a crash mid-run just
resumes from whatever wasn't marked "published" yet.

Expected file shape (YAML or JSON):

    languages:
      zh: {name: "简体中文", platform_tag_style: cn}
      en: {name: "English"}
    publications:
      - pub_id: "ep01-zh-yt-long"
        story_id: "story1"
        episode_code: "ep01"
        lang: "zh"
        platform: "youtube"
        kind: "long"
        asset_key: "ep01.mp4"        # resolved as media_root/asset_key
        title: "..."
        ...

A bare top-level list is also accepted as shorthand for `publications:`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dusk_publisher.errors import AssetNotFoundError, QueueFormatError
from dusk_publisher.models import LanguageMeta, Publication
from dusk_publisher.sources.base import ClaimedAsset, TaskSource


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise QueueFormatError(f"local queue file not found: {path}")
    text = path.read_text()
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise QueueFormatError(f"could not parse {path}: {exc}") from exc
    if isinstance(data, list):
        data = {"publications": data}
    if not isinstance(data, dict):
        raise QueueFormatError(f"{path} must be a mapping or list at the top level")
    return data


def _dump_raw(path: Path, data: dict[str, Any]) -> None:
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


class LocalQueueSource(TaskSource):
    def __init__(self, queue_path: Path, media_root: Path | None = None):
        self.queue_path = Path(queue_path)
        # Default media_root: alongside the queue file, in a `media/` subdir.
        self.media_root = Path(media_root) if media_root else self.queue_path.parent / "media"

    async def fetch_work(self) -> tuple[list[Publication], dict[str, LanguageMeta]]:
        raw = _load_raw(self.queue_path)
        pubs = [Publication(**item) for item in raw.get("publications", [])]
        langs = {k: LanguageMeta(**v) for k, v in (raw.get("languages") or {}).items()}
        pending = [p for p in pubs if p.is_ready_to_run()]
        return pending, langs

    async def all_publications(self) -> list[Publication]:
        """Return *every* record regardless of status (used for parent-gating lookups)."""
        raw = _load_raw(self.queue_path)
        return [Publication(**item) for item in raw.get("publications", [])]

    def _resolve(self, key: str | None) -> Path | None:
        if not key:
            return None
        p = Path(key)
        return p if p.is_absolute() else (self.media_root / p)

    async def claim(self, pub: Publication) -> ClaimedAsset:
        video_path = self._resolve(pub.asset_key)
        if video_path is None or not video_path.exists():
            raise AssetNotFoundError(
                f"[{pub.pub_id}] asset_key {pub.asset_key!r} resolved to "
                f"{video_path} which does not exist (media_root={self.media_root})"
            )
        cover_path = self._resolve(pub.cover_key)
        if cover_path is not None and not cover_path.exists():
            cover_path = None  # cover is optional; missing cover is non-fatal
        return ClaimedAsset(video_path=video_path, cover_path=cover_path)

    async def report(
        self,
        pub_id: str,
        status: str,
        url: str | None = None,
        error: str | None = None,
    ) -> None:
        raw = _load_raw(self.queue_path)
        found = False
        for item in raw.get("publications", []):
            if item.get("pub_id") == pub_id:
                if status == "needs_login":
                    # Mirror the control plane's contract-v1 behavior: a
                    # missing/stale cookie is not a terminal failure, it's
                    # retryable once a human re-logs in. Roll the row back to
                    # `approved` so the next `queue run` picks it up again
                    # automatically, instead of leaving it stuck forever.
                    item["status"] = "approved"
                else:
                    item["status"] = status
                if url is not None:
                    item["url"] = url
                item["error"] = error
                if status == "published":
                    item["published_at"] = datetime.now(timezone.utc).isoformat()
                found = True
                break
        if not found:
            raise QueueFormatError(f"report(): pub_id {pub_id!r} not found in {self.queue_path}")
        _dump_raw(self.queue_path, raw)

    async def heartbeat(self, accounts: list[dict], health: dict | None = None) -> None:
        # No control plane to notify in local mode; caller (CLI/daemon) logs instead.
        return None
