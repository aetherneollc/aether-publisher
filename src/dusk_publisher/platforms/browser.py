"""Shared patchright session helpers + step wrappers used by every adapter.

patchright (https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python, MIT)
is a drop-in replacement for Playwright's async API with stealth patches
applied, which is exactly why `social-auto-upload` (see
THIRD_PARTY_NOTICES.md) standardized on it for this kind of long-running,
cookie-based browser automation. We follow the same choice.

All Playwright/patchright imports are deliberately *lazy* (inside functions),
so importing this module — and therefore importing any adapter — never
requires patchright to be installed. It's only required once code actually
tries to open a browser (`login`, `check`, or a `publish` that already
passed the cookie-file gate). That's what keeps the local-queue pipeline
runnable in the "needs login" self-test without patchright/Chromium present.

Every step helper below raises `StepFailedError(platform, step, selector)`
on failure (never a bare Playwright TimeoutError), per the project's error
message requirement: when a selector goes stale, the error should say
exactly which step and which selector broke.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dusk_publisher.errors import StepFailedError


async def new_session(
    *,
    storage_state: Path | None,
    headless: bool,
    proxy: str | None = None,
    channel: str = "chrome",
):
    """Launch a fresh browser + context (+ page). Caller must call `close_session`."""
    from patchright.async_api import async_playwright  # lazy import, see module docstring

    playwright = await async_playwright().start()
    launch_kwargs: dict[str, Any] = {"headless": headless, "channel": channel}
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    browser = await playwright.chromium.launch(**launch_kwargs)
    context_kwargs: dict[str, Any] = {}
    if storage_state and Path(storage_state).exists():
        context_kwargs["storage_state"] = str(storage_state)
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    page.set_default_timeout(30000)
    return playwright, browser, context, page


async def close_session(playwright, browser) -> None:
    try:
        await browser.close()
    finally:
        await playwright.stop()


async def save_storage_state(context, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(path))


# --- step helpers: every one raises StepFailedError with (platform, step, selector) ---


async def click_step(page, platform: str, step: str, selector: str, *, timeout: int = 15000) -> None:
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click()
    except Exception as exc:  # noqa: BLE001 - intentionally broad, re-raised with context
        raise StepFailedError(platform, step, selector, exc) from exc


async def click_if_present(page, step: str, selector: str, *, timeout: int = 4000) -> bool:
    """Non-fatal variant for optional UI (e.g. dismiss a dropdown, close a dialog).

    Returns False instead of raising if the element never appears — the
    caller decides whether that's actually a problem worth surfacing.
    """
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click()
        return True
    except Exception:
        return False


async def fill_step(page, platform: str, step: str, selector: str, text: str, *, timeout: int = 15000) -> None:
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click()
        try:
            await loc.fill(text)
        except Exception:
            # contenteditable divs (YouTube Studio title/description, X compose box)
            # sometimes reject fill(); fall back to keyboard typing.
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await loc.type(text, delay=4)
    except Exception as exc:  # noqa: BLE001
        raise StepFailedError(platform, step, selector, exc) from exc


async def set_files_step(
    page, platform: str, step: str, selector: str, paths: list[str], *, timeout: int = 60000, state: str = "attached"
) -> None:
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state=state, timeout=timeout)
        await loc.set_input_files(paths)
    except Exception as exc:  # noqa: BLE001
        raise StepFailedError(platform, step, selector, exc) from exc


async def wait_visible_step(page, platform: str, step: str, selector: str, *, timeout: int = 30000) -> None:
    try:
        await page.locator(selector).first.wait_for(state="visible", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise StepFailedError(platform, step, selector, exc) from exc


async def is_logged_out_url(page, login_url_markers: list[str]) -> bool:
    url = page.url
    return any(marker in url for marker in login_url_markers)
