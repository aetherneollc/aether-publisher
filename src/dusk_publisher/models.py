"""Shared data models.

`Publication` mirrors the publication record shape from the 暮色工房 shared
contract v1 verbatim (see project README "Contract" section / task brief),
so the same model works whether records come from the control plane
(`/ops/work`) or a local YAML/JSON queue file. Local-queue-only fields are
optional additions that do not exist in the control-plane contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class Platform(str, Enum):
    youtube = "youtube"
    tiktok = "tiktok"
    x = "x"


class Kind(str, Enum):
    long = "long"
    short = "short"


class PubStatus(str, Enum):
    approved = "approved"
    claimed = "claimed"
    published = "published"
    failed = "failed"
    # Distinct from `failed`: the account has no valid cookie. Per contract
    # v1 update, this rolls the row back to `approved` for retry once a
    # human re-logs in (control plane's job, not ours) rather than treating
    # it as a hard failure. See pipeline/runner.py.
    needs_login = "needs_login"


class Visibility(str, Enum):
    public = "public"
    unlisted = "unlisted"
    private = "private"


class LanguageMeta(BaseModel):
    """One entry of the `languages` map returned by `/ops/work`."""

    name: str
    platform_tag_style: Optional[str] = None

    model_config = {"extra": "allow"}


class Publication(BaseModel):
    """A single publish task, per the shared contract's publication record.

    Field names and types intentionally match the contract exactly:
    pub_id, story_id, episode_code, lang, platform, account, kind, asset_key,
    cover_key, title, description, tags, status, url, parent_pub_id,
    scheduled_at, published_at, error.
    """

    pub_id: str
    story_id: str
    episode_code: str
    lang: str
    platform: Platform
    account: Optional[str] = None
    kind: Kind
    asset_key: str
    cover_key: Optional[str] = None
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    status: PubStatus = PubStatus.approved
    url: Optional[str] = None
    parent_pub_id: Optional[str] = None
    # Denormalized by the control plane (contract v1 fix, adopted after we
    # flagged that /ops/work's approved-only filter made a short's parent
    # long-form URL unreachable): the parent's `url` once it is published,
    # copied directly onto the short's own row. Local queue mode still
    # resolves this itself via parent_pub_id (see pipeline/dropback.py) and
    # ignores this field; ControlPlaneSource uses it directly since it has
    # no way to look up other rows by id. See README "Shared contract".
    parent_url: Optional[str] = None
    scheduled_at: Optional[str] = None
    published_at: Optional[str] = None
    error: Optional[str] = None

    # --- local-queue-only extensions (ignored by the control plane path) ---
    visibility: Visibility = Visibility.public
    playlist: Optional[str] = None

    model_config = {"extra": "allow"}

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return list(v)

    def is_ready_to_run(self) -> bool:
        return self.status == PubStatus.approved

    def is_gated_on_parent(self) -> bool:
        return self.kind == Kind.short and bool(self.parent_pub_id)


class PublishResult(BaseModel):
    ok: bool
    url: Optional[str] = None
    error: Optional[str] = None
    published_at: Optional[datetime] = None


class CheckResult(BaseModel):
    ok: bool
    """True == cookie is present and still authenticates."""
    detail: str = ""


class LoginResult(BaseModel):
    ok: bool
    detail: str = ""
    account_file: Optional[str] = None
