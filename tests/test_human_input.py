"""Human pauses, per-character typing, and pre/post browsing. No browser."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from dusk_publisher.platforms.browser import fill_step
from dusk_publisher.platforms.human import (
    AFTER_POST_SECONDS,
    BEFORE_POST_SECONDS,
    browse_after_post,
    browse_before_upload,
    character_delay_ms,
    human_pause_ms,
)


def test_pause_and_typing_delays_stay_in_range():
    random.seed(0)
    pauses = [human_pause_ms() for _ in range(40)]
    assert all(200 <= item <= 700 for item in pauses)
    letters = [character_delay_ms("a") for _ in range(40)]
    assert all(40 <= item <= 140 for item in letters)
    marks = [character_delay_ms(".") for _ in range(40)]
    assert all(240 <= item <= 640 for item in marks)


class _Page:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.keyboard = self
        self.mouse = self

    def locator(self, selector: str):
        self.events.append(("locator", selector))

        class _Handle:
            first = self

        _Handle.first = _Locator(self)
        return _Handle()

    async def wait_for_timeout(self, ms: int) -> None:
        self.events.append(("wait", ms))

    async def press(self, key: str) -> None:
        self.events.append(("press", key))

    async def type(self, text: str) -> None:
        self.events.append(("type", text))

    async def wheel(self, x: int, y: int) -> None:
        self.events.append(("wheel", x, y))

    async def goto(self, url: str, **kwargs) -> None:
        self.events.append(("goto", url))

    async def fill(self, text: str) -> None:
        self.events.append(("fill", text))


class _Locator:
    def __init__(self, page: _Page) -> None:
        self.page = page

    async def wait_for(self, **kwargs) -> None:
        self.page.events.append(("wait_for", kwargs.get("state")))

    async def click(self) -> None:
        self.page.events.append(("click",))

    async def fill(self, text: str) -> None:
        self.page.events.append(("fill", text))


def _low(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dusk_publisher.platforms.human.random.randint", lambda low, high: low)
    monkeypatch.setattr("dusk_publisher.platforms.human.random.uniform", lambda low, high: low)


@pytest.mark.asyncio
async def test_fill_step_types_each_character_and_does_not_fill(monkeypatch: pytest.MonkeyPatch):
    _low(monkeypatch)
    page = _Page()
    await fill_step(page, "youtube", "fill title", "#title", "Hi!")
    assert not any(event[0] == "fill" for event in page.events)
    typed = [event[1] for event in page.events if event[0] == "type"]
    assert typed == ["H", "i", "!"]
    waits = [event[1] for event in page.events if event[0] == "wait"]
    assert waits[0] == 200
    assert waits[1:] == [40, 40, 240]
    assert ("press", "Control+A") in page.events
    assert ("press", "Delete") in page.events
    assert ("click",) in page.events


@pytest.mark.asyncio
async def test_browse_before_and_after_post(monkeypatch: pytest.MonkeyPatch):
    _low(monkeypatch)
    page = _Page()
    await browse_before_upload(page, "https://www.youtube.com/")
    assert ("goto", "https://www.youtube.com/") in page.events
    wheels = [event for event in page.events if event[0] == "wheel"]
    assert len(wheels) == 2
    waits = [event[1] for event in page.events if event[0] == "wait"]
    assert int(BEFORE_POST_SECONDS[0] * 1000) in waits

    page.events.clear()
    await browse_after_post(page, "https://www.tiktok.com/")
    assert ("goto", "https://www.tiktok.com/") in page.events
    waits = [event[1] for event in page.events if event[0] == "wait"]
    assert int(AFTER_POST_SECONDS[0] * 1000) in waits


def test_adapters_do_not_fill_visible_text():
    root = Path(__file__).resolve().parents[1] / "src" / "dusk_publisher" / "platforms"
    for rel in ("browser.py", "youtube/adapter.py", "tiktok/adapter.py", "x/adapter.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert ".fill(" not in text
        assert "delay=4" not in text
        assert "delay=6" not in text
    browser = (root / "browser.py").read_text(encoding="utf-8")
    youtube = (root / "youtube" / "adapter.py").read_text(encoding="utf-8")
    tiktok = (root / "tiktok" / "adapter.py").read_text(encoding="utf-8")
    x_adapter = (root / "x" / "adapter.py").read_text(encoding="utf-8")
    assert "set_input_files" in browser
    assert "browse_before_upload" in youtube
    assert "browse_after_post" in youtube
    assert "browse_before_upload" in tiktok
    assert "browse_before_upload" in x_adapter
