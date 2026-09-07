"""YouTube adapter — Studio browser automation (deliberately NOT the Data API).

Why browser automation and not the official YouTube Data API: videos
uploaded through an *unaudited* Google API project are force-locked to
"private" and cannot be made public without passing Google's compliance
audit, which is impractical for a personal/single channel. Browser
automation has no such restriction. This rationale, the interactive-login
shape, the "wait for the progress bar to hit 100% before clicking Publish"
rule, and a substantial part of the selector set below are adapted from the
MIT-licensed `social-auto-upload` project — see ../../../THIRD_PARTY_NOTICES.md.

Only used for `kind=long` per this project's routing (see pipeline/dropback.py):
YouTube carries the "正片" (feature episodes), TikTok/X carry the "短片"
(shorts) that link back to it.
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
    wait_visible_step,
)
from dusk_publisher.platforms.youtube.selectors import (
    LOGGED_IN_URL_MARKER,
    LOGIN_URL_MARKERS,
    SEL,
    STUDIO_URL,
    UPLOAD_DONE_MARKERS,
    UPLOAD_URL,
    VISIBILITY_RADIO_NAME,
)

log = get_logger(__name__)

LOGIN_WAIT_SECONDS = 600  # 10 minutes, matches social-auto-upload's budget
UPLOAD_WAIT_MAX_POLLS = 360  # 360 * 5s = 30 minutes


async def _dismiss_autocomplete(page) -> None:
    """Close the '#hashtag' / '@mention' autocomplete flyout in title/description
    boxes. It otherwise sits on top of the Next/Publish buttons and blocks clicks."""
    try:
        await page.evaluate("() => { const a = document.activeElement; if (a && a.blur) a.blur(); }")
    except Exception:
        pass
    try:
        dropdown = page.locator(SEL["autocomplete_dropdown"])
        if await dropdown.count() > 0:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
    except Exception:
        pass


async def _fill_richtext(page, platform: str, step: str, selector: str, text: str) -> None:
    await fill_step(page, platform, step, selector, text)
    await page.wait_for_timeout(400)
    await _dismiss_autocomplete(page)


async def _wait_upload_complete(page, platform: str) -> bool:
    """Poll the upload progress label until it reads as done.

    Critically: browser-driven uploads stream while the tab stays open. If
    Publish is clicked (and/or the browser closed) before the progress bar
    reaches 100%, the upload gets cut off mid-transfer instead of completing
    (this is the #1 failure mode `social-auto-upload` calls out, and the
    reason this function exists at all instead of just clicking Publish
    right after selecting the file).
    """
    last = ""
    for _ in range(UPLOAD_WAIT_MAX_POLLS):
        txt = ""
        loc = page.locator(SEL["upload_progress_text"]).first
        try:
            if await loc.count():
                txt = (await loc.inner_text()).strip()
        except Exception:
            pass
        if txt:
            if any(marker in txt for marker in UPLOAD_DONE_MARKERS):
                log.info("[%s] upload complete: %s", platform, txt[:60])
                return True
            if txt != last:
                log.info("[%s] uploading: %s", platform, txt[:60])
                last = txt
        await page.wait_for_timeout(5000)
    log.warning("[%s] timed out waiting for upload to reach 100%% after 30min; attempting publish anyway", platform)
    return False


class YouTubeAdapter(PlatformAdapter):
    name = "youtube"

    async def login(self, account: str) -> LoginResult:
        cookie_file = self.cookie_file(account)
        # Login must always be headed: a human has to type credentials / 2FA.
        playwright, browser, context, page = await new_session(
            storage_state=None, headless=False, proxy=self.settings.yt_proxy
        )
        try:
            await page.goto(STUDIO_URL, wait_until="domcontentloaded")
            log.info("[youtube:%s] please sign in to Google/YouTube in the opened browser window", account)
            ok = False
            for _ in range(LOGIN_WAIT_SECONDS):
                if LOGGED_IN_URL_MARKER in page.url:
                    await page.wait_for_timeout(2000)  # let cookies settle
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
            storage_state=cookie_file, headless=True, proxy=self.settings.yt_proxy
        )
        try:
            await page.goto(STUDIO_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                return CheckResult(ok=False, detail="redirected to Google sign-in, cookie is stale")
            if LOGGED_IN_URL_MARKER in page.url:
                return CheckResult(ok=True, detail="cookie valid")
            return CheckResult(ok=False, detail=f"unexpected URL after opening Studio: {page.url}")
        except Exception as exc:
            return CheckResult(ok=False, detail=f"check failed: {exc}")
        finally:
            await close_session(playwright, browser)

    async def _publish_impl(self, account: str, req: PublishRequest) -> PublishResult:
        cookie_file = self.cookie_file(account)
        playwright, browser, context, page = await new_session(
            storage_state=cookie_file, headless=self.settings.headless, proxy=self.settings.yt_proxy
        )
        try:
            log.info("[youtube:%s] uploading %s", account, Path(req.video_path).name)
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                return PublishResult(ok=False, error="cookie expired mid-run; needs re-login")

            await set_files_step(page, self.name, "select video file", SEL["file_input"], [str(req.video_path)])
            await wait_visible_step(page, self.name, "wait for upload details dialog", SEL["title_textarea_wrapper"], timeout=120000)

            await _fill_richtext(page, self.name, "fill title", SEL["title_textbox"], req.title[:100])
            if req.description.strip():
                await _fill_richtext(page, self.name, "fill description", SEL["description_textbox"], req.description)

            if req.cover_path and Path(req.cover_path).exists():
                try:
                    await set_files_step(page, self.name, "upload thumbnail", SEL["thumbnail_input"], [str(req.cover_path)], timeout=20000)
                    await page.wait_for_timeout(2000)
                except Exception as exc:
                    log.warning("[youtube:%s] thumbnail upload skipped (non-fatal): %s", account, exc)

            if req.playlist:
                await self._add_to_playlist(page, req.playlist)

            if not await click_if_present(page, SEL["audience_not_for_kids"], timeout=10000):
                await click_if_present(page, SEL["audience_not_for_kids_fallback"], timeout=6000)

            if req.tags:
                try:
                    await click_if_present(page, SEL["tags_toggle_more"], timeout=6000)
                    await page.wait_for_timeout(800)
                    tag_input = page.locator(SEL["tags_input"]).first
                    await tag_input.click()
                    await tag_input.type(",".join(req.tags)[:500] + ",", delay=4)
                except Exception as exc:
                    log.warning("[youtube:%s] tags input skipped (non-fatal): %s", account, exc)

            for _ in range(5):
                vis_probe = page.locator(SEL["visibility_radio_public_probe"])
                if await vis_probe.count() and await vis_probe.first.is_visible():
                    break
                if not await click_if_present(page, SEL["next_button"], timeout=6000):
                    await page.wait_for_timeout(1200)
                await page.wait_for_timeout(1000)

            radio_name = VISIBILITY_RADIO_NAME[req.visibility.value]
            await click_step(page, self.name, "set visibility", SEL["visibility_radio"].format(name=radio_name), timeout=10000)

            log.info("[youtube:%s] waiting for upload to finish before publishing", account)
            await _wait_upload_complete(page, self.name)

            await page.wait_for_timeout(1200)
            published = await click_if_present(page, SEL["done_button"], timeout=15000)
            video_url = None
            if published:
                await page.wait_for_timeout(4000)
                link = page.locator(SEL["published_video_link"]).first
                if await link.count():
                    video_url = await link.get_attribute("href")
                await click_if_present(page, SEL["close_button"], timeout=8000)
            else:
                return PublishResult(
                    ok=False,
                    error="publish button (#done-button) not found — upload may not have reached a publishable state",
                )

            try:
                await save_storage_state(context, cookie_file)  # refresh cookie
            except Exception:
                pass
            return PublishResult(ok=True, url=video_url)
        finally:
            await close_session(playwright, browser)

    async def _add_to_playlist(self, page, playlist: str) -> None:
        """Best-effort: join (or create) a playlist. Non-fatal — a failure here
        should never block publishing the video itself, matching upstream's
        treatment of this step."""
        try:
            await click_if_present(page, SEL["playlist_dropdown_trigger"], timeout=8000)
            await page.wait_for_timeout(1200)
            existing = page.locator(
                f"tp-yt-paper-checkbox:has-text('{playlist}'), ytcp-checkbox-group:has-text('{playlist}')"
            ).first
            if await existing.count():
                await existing.click()
            else:
                if await click_if_present(page, SEL["playlist_new_button"], timeout=4000):
                    await page.wait_for_timeout(800)
                    await click_if_present(page, SEL["playlist_new_menu_item"], timeout=3000)
                    title_box = page.locator(SEL["playlist_title_box"]).first
                    if await title_box.count():
                        await title_box.click()
                        await title_box.type(playlist, delay=6)
                    await click_if_present(page, SEL["playlist_create_button"], timeout=4000)
        except Exception as exc:
            log.warning("[youtube] playlist step skipped (non-fatal): %s", exc)
        finally:
            await click_if_present(page, SEL["playlist_done_button"], timeout=3000)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await page.wait_for_timeout(600)
