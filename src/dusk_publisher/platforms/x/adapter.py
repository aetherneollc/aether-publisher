"""X (Twitter) adapter — browser automation, original implementation.

The reference project (`social-auto-upload`, see THIRD_PARTY_NOTICES.md)
does not support X/Twitter at all, so unlike the YouTube adapter, nothing
here is ported — only the overall login/check/publish shape and the
cookie-file convention are kept consistent with the other two platforms.

X has no separate "description" field distinct from the post text itself,
so `req.title` + `req.description` are concatenated into the tweet text
(truncated to 280 chars), and — per the dropback requirement — the parent
long-form video URL is included directly in the tweet text (not just a
reply) since that's the only guaranteed-visible place on this platform.
We additionally attempt a best-effort self-reply + "Highlight" as the
closest analog to a pinned top comment; see selectors.py for why that part
is flagged as fragile.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dusk_publisher.logging_setup import get_logger
from dusk_publisher.models import CheckResult, LoginResult, PublishResult
from dusk_publisher.platforms.base import PlatformAdapter, PublishRequest
from dusk_publisher.platforms.browser import (
    click_if_present,
    click_step,
    close_session,
    fill_step,
    new_session,
    save_storage_state,
    set_files_step,
)
from dusk_publisher.platforms.x.selectors import HOME_URL, LOGIN_URL, LOGIN_URL_MARKERS, SEL

log = get_logger(__name__)

LOGIN_WAIT_SECONDS = 600
MEDIA_PROCESSING_MAX_POLLS = 120  # 120 * 3s = 6 minutes; X shorts are small


def _build_tweet_text(req: PublishRequest) -> str:
    """Tweet text. `req.description` already has the dropback line injected
    by the pipeline runner (see pipeline/dropback.py) when `parent_url` is
    set — X has no separate description field, so that injected line IS
    part of the tweet body. We don't append parent_url again here."""
    parts = [p for p in (req.title.strip(), req.description.strip()) if p]
    text = " — ".join(parts) if parts else ""
    if req.tags:
        text = (text + " " if text else "") + " ".join(f"#{t.lstrip('#')}" for t in req.tags)
    return text[:280]


class XAdapter(PlatformAdapter):
    name = "x"

    async def login(self, account: str) -> LoginResult:
        cookie_file = self.cookie_file(account)
        playwright, browser, context, page = await new_session(
            storage_state=None, headless=False, proxy=self.settings.x_proxy
        )
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            log.info("[x:%s] please sign in in the opened browser window", account)
            ok = False
            for _ in range(LOGIN_WAIT_SECONDS):
                if not any(marker in page.url for marker in LOGIN_URL_MARKERS):
                    compose = page.locator(SEL["compose_button"])
                    if await compose.count() > 0:
                        await page.wait_for_timeout(2000)
                        ok = True
                        break
                await asyncio.sleep(1)
            if ok:
                await save_storage_state(context, cookie_file)
                return LoginResult(ok=True, detail="login succeeded", account_file=str(cookie_file))
            return LoginResult(ok=False, detail=f"timed out waiting for login after {LOGIN_WAIT_SECONDS}s")
        finally:
            await close_session(playwright, browser)

    async def check(self, account: str) -> CheckResult:
        cookie_file = self.cookie_file(account)
        if not cookie_file.exists():
            return CheckResult(ok=False, detail="no cookie file, run login")
        playwright, browser, context, page = await new_session(
            storage_state=cookie_file, headless=True, proxy=self.settings.x_proxy
        )
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                return CheckResult(ok=False, detail="redirected to login, cookie is stale")
            compose = page.locator(SEL["compose_button"])
            if await compose.count() > 0:
                return CheckResult(ok=True, detail="cookie valid")
            return CheckResult(ok=False, detail=f"compose button not found at {page.url}; cookie likely stale")
        except Exception as exc:
            return CheckResult(ok=False, detail=f"check failed: {exc}")
        finally:
            await close_session(playwright, browser)

    async def _publish_impl(self, account: str, req: PublishRequest) -> PublishResult:
        cookie_file = self.cookie_file(account)
        playwright, browser, context, page = await new_session(
            storage_state=cookie_file, headless=self.settings.headless, proxy=self.settings.x_proxy
        )
        try:
            log.info("[x:%s] posting %s", account, Path(req.video_path).name)
            await page.goto(HOME_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                return PublishResult(ok=False, error="cookie expired mid-run; needs re-login")

            await click_step(page, self.name, "open composer", SEL["compose_button"], timeout=15000)
            await fill_step(page, self.name, "fill tweet text", SEL["tweet_textarea"], _build_tweet_text(req))
            await set_files_step(page, self.name, "attach video", SEL["file_input"], [str(req.video_path)], timeout=60000)

            log.info("[x:%s] waiting for media processing before posting", account)
            await self._wait_media_processed(page)

            await click_step(page, self.name, "click post", SEL["post_button"], timeout=20000)
            await page.wait_for_timeout(3000)

            tweet_url = await self._find_posted_tweet_url(page)

            if req.parent_url and tweet_url:
                await self._highlight_dropback_reply(page, tweet_url, req.parent_url)

            try:
                await save_storage_state(context, cookie_file)
            except Exception:
                pass
            return PublishResult(ok=True, url=tweet_url)
        finally:
            await close_session(playwright, browser)

    async def _wait_media_processed(self, page) -> None:
        for _ in range(MEDIA_PROCESSING_MAX_POLLS):
            spinner = page.locator(SEL["media_processing_spinner"]).first
            still_processing = await spinner.count() > 0 and await spinner.is_visible()
            if not still_processing:
                return
            await page.wait_for_timeout(3000)
        log.warning("[x] timed out waiting for video processing to finish; attempting to post anyway")

    async def _find_posted_tweet_url(self, page) -> str | None:
        try:
            toast_link = page.locator(SEL["toast_view_link"]).first
            if await toast_link.count():
                href = await toast_link.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"https://x.com{href}"
        except Exception:
            pass
        try:
            await click_if_present(page, SEL["profile_link"], timeout=8000)
            await page.wait_for_timeout(1500)
            first = page.locator(SEL["first_tweet_permalink"]).first
            if await first.count():
                href = await first.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"https://x.com{href}"
        except Exception as exc:
            log.warning("[x] could not resolve posted tweet URL (non-fatal): %s", exc)
        return None

    async def _highlight_dropback_reply(self, page, tweet_url: str, parent_url: str) -> None:
        """Best-effort analog of a "pinned top comment": reply to our own
        post with the parent (long-form) video link, then try to mark that
        reply as a Highlight. Never fatal — publish already succeeded."""
        try:
            await page.goto(tweet_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            await click_step(page, self.name, "open reply box", SEL["reply_button_on_tweet"], timeout=10000)
            await fill_step(page, self.name, "fill dropback reply", SEL["tweet_textarea"], f"完整版/full video: {parent_url}")
            await click_step(page, self.name, "submit dropback reply", SEL["post_button"], timeout=10000)
            await page.wait_for_timeout(2000)
            if await click_if_present(page, SEL["reply_caret_menu"], timeout=5000):
                await click_if_present(page, SEL["highlight_menu_item"], timeout=4000)
        except Exception as exc:
            log.warning("[x] highlighted dropback reply skipped (non-fatal): %s", exc)
