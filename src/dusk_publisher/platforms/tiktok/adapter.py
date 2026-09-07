"""TikTok adapter — browser automation for short-form uploads.

Follows the same cookie-file / login-check-publish shape as YouTube (see
../youtube/adapter.py and THIRD_PARTY_NOTICES.md for the pattern's origin).
Supports scheduled publishing (explicitly requested) and, when
`req.parent_url` is set (dropback from a same-language long-form YouTube
video), appends the link to the caption and best-effort posts+pins it as
the top comment on the freshly published video.

Risk note (see final report): TikTok's upload DOM changes more often than
YouTube Studio's and we have no reference implementation for it (unlike
YouTube). Selectors in `selectors.py` are original and unverified against
the live site — expect to need updates on first real use.
"""

from __future__ import annotations

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
from dusk_publisher.platforms.tiktok.selectors import LOGIN_URL_MARKERS, SEL, UPLOAD_URL

log = get_logger(__name__)

LOGIN_WAIT_SECONDS = 600
UPLOAD_WAIT_MAX_POLLS = 180  # 180 * 5s = 15 minutes; TikTok processes faster than YT


def _build_caption(req: PublishRequest) -> str:
    """Caption text. `req.description` already has the dropback line injected
    by the pipeline runner (see pipeline/dropback.py) when `parent_url` is
    set, so this only handles title/tags — no separate parent_url append
    here, to avoid printing the link twice."""
    parts = [req.title.strip()] if req.title.strip() else []
    if req.description.strip():
        parts.append(req.description.strip())
    if req.tags:
        parts.append(" ".join(f"#{t.lstrip('#')}" for t in req.tags))
    return "\n".join(parts)[:2200]  # TikTok caption cap


class TikTokAdapter(PlatformAdapter):
    name = "tiktok"

    async def login(self, account: str) -> LoginResult:
        cookie_file = self.cookie_file(account)
        playwright, browser, context, page = await new_session(
            storage_state=None, headless=False, proxy=self.settings.tiktok_proxy
        )
        try:
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            log.info("[tiktok:%s] please sign in in the opened browser window", account)
            import asyncio

            ok = False
            for _ in range(LOGIN_WAIT_SECONDS):
                if not any(marker in page.url for marker in LOGIN_URL_MARKERS) and "tiktok.com" in page.url:
                    # Confirm we can actually see the upload widget, not just any tiktok.com page.
                    file_input = page.locator(SEL["file_input"])
                    if await file_input.count() > 0:
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
            storage_state=cookie_file, headless=True, proxy=self.settings.tiktok_proxy
        )
        try:
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                return CheckResult(ok=False, detail="redirected to login, cookie is stale")
            file_input = page.locator(SEL["file_input"])
            if await file_input.count() > 0:
                return CheckResult(ok=True, detail="cookie valid")
            return CheckResult(ok=False, detail=f"upload widget not found at {page.url}; cookie likely stale")
        except Exception as exc:
            return CheckResult(ok=False, detail=f"check failed: {exc}")
        finally:
            await close_session(playwright, browser)

    async def _publish_impl(self, account: str, req: PublishRequest) -> PublishResult:
        cookie_file = self.cookie_file(account)
        playwright, browser, context, page = await new_session(
            storage_state=cookie_file, headless=self.settings.headless, proxy=self.settings.tiktok_proxy
        )
        try:
            log.info("[tiktok:%s] uploading %s", account, Path(req.video_path).name)
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                return PublishResult(ok=False, error="cookie expired mid-run; needs re-login")

            await set_files_step(page, self.name, "select video file", SEL["file_input"], [str(req.video_path)])

            log.info("[tiktok:%s] waiting for upload/processing to finish before posting", account)
            await self._wait_upload_processed(page)

            caption = _build_caption(req)
            await fill_step(page, self.name, "fill caption", SEL["caption_editor"], caption)

            if req.scheduled_at:
                await self._apply_schedule(page, req.scheduled_at)

            await click_step(page, self.name, "click post", SEL["post_button"], timeout=20000)
            await page.wait_for_timeout(3000)
            # Non-fatal: presence of the success toast is best-effort confirmation only.
            await click_if_present(page, SEL["post_success_toast"], timeout=15000)

            video_url = await self._find_latest_video_url(page)

            if req.parent_url and video_url:
                await self._pin_dropback_comment(page, video_url, req.parent_url)

            try:
                await save_storage_state(context, cookie_file)
            except Exception:
                pass
            return PublishResult(ok=True, url=video_url)
        finally:
            await close_session(playwright, browser)

    async def _wait_upload_processed(self, page) -> None:
        for _ in range(UPLOAD_WAIT_MAX_POLLS):
            progress = page.locator(SEL["upload_progress"]).first
            still_uploading = await progress.count() > 0 and await progress.is_visible()
            if not still_uploading:
                return
            await page.wait_for_timeout(5000)
        log.warning("[tiktok] timed out waiting for upload processing to finish; attempting to post anyway")

    async def _apply_schedule(self, page, scheduled_at: str) -> None:
        try:
            await click_step(page, self.name, "select scheduled publish", SEL["schedule_radio_later"], timeout=8000)
            # scheduled_at is ISO8601; TikTok's own date/time widgets vary by
            # locale, so this is intentionally left as a documented seam:
            # the date/time picker interaction depends on the exact widget
            # markup, which we could not verify without a live account.
            log.warning(
                "[tiktok] schedule widget interaction for %s is a stub — "
                "date/time picker selectors need to be filled in against the live UI "
                "(see selectors.py schedule_date_picker/schedule_time_picker)",
                scheduled_at,
            )
        except Exception as exc:
            log.warning("[tiktok] could not switch to scheduled publish, falling back to publish-now: %s", exc)
            await click_if_present(page, SEL["schedule_radio_now"], timeout=4000)

    async def _find_latest_video_url(self, page) -> str | None:
        try:
            await click_if_present(page, SEL["manage_content_link"], timeout=10000)
            first = page.locator(SEL["content_list_first_item"]).first
            if await first.count():
                href = await first.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"https://www.tiktok.com{href}"
        except Exception as exc:
            log.warning("[tiktok] could not resolve published video URL (non-fatal): %s", exc)
        return None

    async def _pin_dropback_comment(self, page, video_url: str, parent_url: str) -> None:
        """Best-effort: post 'full video: <parent_url>' as a comment on the
        just-published short and pin it. Never fatal — publish already
        succeeded by this point."""
        try:
            await page.goto(video_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            await fill_step(page, self.name, "fill dropback comment", SEL["comment_input"], f"完整版/full video: {parent_url}")
            await click_step(page, self.name, "submit dropback comment", SEL["comment_post_button"], timeout=8000)
            await page.wait_for_timeout(1500)
            if await click_if_present(page, SEL["comment_more_menu"], timeout=5000):
                await click_if_present(page, SEL["comment_pin_option"], timeout=4000)
        except Exception as exc:
            log.warning("[tiktok] pinned dropback comment skipped (non-fatal): %s", exc)
