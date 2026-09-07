"""Common interface every platform adapter (youtube/tiktok/x) implements.

Three commands per platform, per the brief:
  - login(account):   interactive, headed, saves a cookie file.
  - check(account):    headless cookie validity probe.
  - publish(account, req): the actual upload/post flow.

`publish()` is split into `_require_cookie()` (fast, browser-less gate) and
the subclass's own `_publish_impl()` so that "no cookie yet" fails
immediately with `NeedsLoginError`, without ever importing patchright or
opening a network connection. That is what makes the local-queue
self-test able to run for real, end-to-end, in an environment with no
browser installed and no logged-in accounts: it proves out queue parsing,
gating, routing, and asset resolution, and then stops cleanly at "needs
login" exactly where a real operator would need to intervene.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from dusk_publisher.accounts.cookies import cookie_path, has_valid_cookie_file
from dusk_publisher.errors import NeedsLoginError
from dusk_publisher.models import CheckResult, LanguageMeta, LoginResult, PublishResult, Visibility
from dusk_publisher.settings import Settings


class PublishRequest(BaseModel):
    video_path: Path
    cover_path: Path | None = None
    title: str = ""
    description: str = ""
    tags: list[str] = []
    lang: str = ""
    visibility: Visibility = Visibility.public
    playlist: str | None = None
    scheduled_at: str | None = None
    # Dropback: URL of the same-language long video this short should promote.
    # When set, adapters that support it append it to the description and
    # attempt to post+pin it as a top comment/reply after publishing.
    parent_url: str | None = None
    language_meta: LanguageMeta | None = None

    model_config = {"arbitrary_types_allowed": True}


class PlatformAdapter(ABC):
    name: ClassVar[str] = "base"

    def __init__(self, settings: Settings):
        self.settings = settings

    def cookie_file(self, account: str) -> Path:
        return cookie_path(self.settings.data_dir, self.name, account)

    def _require_cookie(self, account: str) -> Path:
        path = self.cookie_file(account)
        if not has_valid_cookie_file(path):
            raise NeedsLoginError(self.name, account)
        return path

    @abstractmethod
    async def login(self, account: str) -> LoginResult:
        """Open a headed browser, let a human sign in, save storage_state."""

    @abstractmethod
    async def check(self, account: str) -> CheckResult:
        """Headless probe: is the saved cookie still accepted?"""

    async def publish(self, account: str, req: PublishRequest) -> PublishResult:
        self._require_cookie(account)  # raises NeedsLoginError, no browser touched
        return await self._publish_impl(account, req)

    @abstractmethod
    async def _publish_impl(self, account: str, req: PublishRequest) -> PublishResult:
        """Actual upload flow. Only called once a cookie file is confirmed present."""
