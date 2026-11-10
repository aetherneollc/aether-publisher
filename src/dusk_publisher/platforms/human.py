"""Pauses and per-character typing shared by YouTube, TikTok, and X.

Clicks wait 200–700ms. Visible text is typed one character at a time with
40–140ms between characters and a longer pause after punctuation. File inputs
stay on ``set_input_files``; this module does not fill them.
"""

from __future__ import annotations

import random

CLICK_PAUSE_MS = (200, 700)
TYPE_DELAY_MS = (40, 140)
PUNCTUATION_PAUSE_MS = (200, 500)
BEFORE_POST_SECONDS = (15.0, 35.0)
AFTER_POST_SECONDS = (8.0, 16.0)

_PUNCTUATION = set(".,!?;:，。！？、；：…")


def human_pause_ms() -> int:
    low, high = CLICK_PAUSE_MS
    return random.randint(low, high)


def character_delay_ms(char: str) -> int:
    low, high = TYPE_DELAY_MS
    delay = random.randint(low, high)
    if char in _PUNCTUATION:
        extra_low, extra_high = PUNCTUATION_PAUSE_MS
        delay += random.randint(extra_low, extra_high)
    return delay


async def human_pause(page) -> None:
    await page.wait_for_timeout(human_pause_ms())


async def human_type(page, text: str) -> None:
    """Type ``text`` one character at a time. Does not call ``locator.fill``."""
    for char in text:
        await page.keyboard.type(char)
        await page.wait_for_timeout(character_delay_ms(char))


async def browse_feed(page, url: str, *, min_seconds: float, max_seconds: float) -> None:
    """Open a home or feed, scroll a few times, then stay."""
    await page.goto(url, wait_until="domcontentloaded")
    scrolls = random.randint(2, 4)
    for _ in range(scrolls):
        await page.mouse.wheel(0, random.randint(400, 1200))
        await page.wait_for_timeout(random.randint(250, 700))
    dwell_ms = int(random.uniform(min_seconds, max_seconds) * 1000)
    await page.wait_for_timeout(dwell_ms)


async def browse_before_upload(page, url: str) -> None:
    """Home or feed for about 15–35 seconds before an upload or post."""
    low, high = BEFORE_POST_SECONDS
    await browse_feed(page, url, min_seconds=low, max_seconds=high)


async def browse_after_post(page, url: str) -> None:
    """Return to the feed for a shorter stay after a successful post."""
    low, high = AFTER_POST_SECONDS
    await browse_feed(page, url, min_seconds=low, max_seconds=high)
