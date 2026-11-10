"""Actions Firefox launch options and Xvfb geometry. No real browser is started."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from dusk_publisher.firefox_fingerprint import FirefoxFingerprint
from dusk_publisher.platforms.browser import (
    actions_firefox_launch_plan,
    new_session,
    xvfb_geometry,
)
from dusk_publisher.platforms.browser import _launch_actions_firefox


def test_xvfb_geometry_uses_screen_size_dpr_and_the_default():
    assert xvfb_geometry(None) == "1920x1080"
    assert xvfb_geometry(FirefoxFingerprint()) == "1920x1080"
    assert xvfb_geometry(FirefoxFingerprint(screen_width=1920, screen_height=None)) == "1920x1080"
    assert xvfb_geometry(FirefoxFingerprint(screen_width=None, screen_height=1080)) == "1920x1080"
    assert xvfb_geometry(FirefoxFingerprint(screen_width=1512, screen_height=982)) == "1512x982"
    assert (
        xvfb_geometry(FirefoxFingerprint(screen_width=1512, screen_height=982, device_pixel_ratio=None))
        == "1512x982"
    )
    assert (
        xvfb_geometry(FirefoxFingerprint(screen_width=1512, screen_height=982, device_pixel_ratio=0))
        == "1512x982"
    )
    assert (
        xvfb_geometry(FirefoxFingerprint(screen_width=1512, screen_height=982, device_pixel_ratio=-1))
        == "1512x982"
    )
    assert (
        xvfb_geometry(FirefoxFingerprint(screen_width=1920, screen_height=1080, device_pixel_ratio=1.25))
        == "2400x1350"
    )
    assert (
        xvfb_geometry(FirefoxFingerprint(screen_width=1440, screen_height=900, device_pixel_ratio=2))
        == "2880x1800"
    )
    # 1366 * 1.25 is 1707.5, which rounds to 1708.
    assert (
        xvfb_geometry(FirefoxFingerprint(screen_width=1366, screen_height=768, device_pixel_ratio=1.25))
        == "1708x960"
    )


def test_actions_launch_plan_includes_storage_state_without_a_profile(tmp_path: Path):
    cookie = tmp_path / "account.json"
    cookie.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    fingerprint = FirefoxFingerprint(
        user_agent="Mozilla/5.0 (test)",
        platform="",
        oscpu="",
        languages="",
        timezone="Asia/Shanghai",
        screen_width=1920,
        screen_height=1080,
        device_pixel_ratio=1.25,
        webgl_vendor="",
        webgl_renderer="",
    )
    launch, context = actions_firefox_launch_plan(
        fingerprint,
        storage_state=cookie,
        executable="/usr/lib/firefox/firefox",
        base_env={"DISPLAY": ":99", "PATH": "/usr/bin"},
    )
    assert launch["channel"] == "moz-firefox"
    assert launch["headless"] is False
    assert launch["executable_path"] == "/usr/lib/firefox/firefox"
    assert launch["firefox_user_prefs"]["general.useragent.override"] == "Mozilla/5.0 (test)"
    assert launch["firefox_user_prefs"]["layout.css.devPixelsPerPx"] == 1.25
    assert "general.platform.override" not in launch["firefox_user_prefs"]
    assert "general.oscpu.override" not in launch["firefox_user_prefs"]
    assert launch["env"]["TZ"] == "Asia/Shanghai"
    assert launch["env"]["DISPLAY"] == ":99"
    assert "proxy" not in launch
    assert "user_data_dir" not in launch
    assert context["storage_state"] == str(cookie)
    assert context["no_viewport"] is True
    assert context["timezone_id"] == "Asia/Shanghai"
    assert "proxy" not in context
    assert "extra_http_headers" not in context

    bare_launch, bare_context = actions_firefox_launch_plan(
        None,
        storage_state=tmp_path / "missing.json",
        executable="  ",
    )
    assert bare_launch == {"channel": "moz-firefox", "headless": False}
    assert bare_context == {"no_viewport": True}


def _install_fake_firefox(monkeypatch: pytest.MonkeyPatch, calls: dict):
    class Page:
        def set_default_timeout(self, timeout: int) -> None:
            calls["timeout"] = timeout

    class Context:
        async def new_page(self):
            return Page()

    class Browser:
        async def new_context(self, **kwargs):
            calls["context"] = kwargs
            return Context()

        async def close(self) -> None:
            calls["browser_closed"] = True

    class Firefox:
        async def launch(self, **kwargs):
            calls["launch"] = kwargs
            return Browser()

        async def launch_persistent_context(self, *args, **kwargs):
            calls["persistent"] = (args, kwargs)
            raise AssertionError("actions mode must not launch a Firefox profile directory")

    class Playwright:
        def __init__(self):
            self.firefox = Firefox()

        async def stop(self) -> None:
            calls["stopped"] = True

    class Manager:
        async def start(self):
            return Playwright()

    module = types.ModuleType("patchright.async_api")
    module.async_playwright = lambda: Manager()
    package = types.ModuleType("patchright")
    package.__path__ = []
    package.async_api = module
    monkeypatch.setitem(sys.modules, "patchright", package)
    monkeypatch.setitem(sys.modules, "patchright.async_api", module)


@pytest.mark.asyncio
async def test_actions_launch_uses_storage_state_and_fingerprint_geometry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cookie = tmp_path / "state.json"
    cookie.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    fingerprint = tmp_path / "firefox-fingerprint.yaml"
    fingerprint.write_text(
        "\n".join(
            [
                'user_agent: "Mozilla/5.0 (test)"',
                'platform: ""',
                'timezone: "Asia/Shanghai"',
                "screen_width: 1440",
                "screen_height: 900",
                "device_pixel_ratio: 2",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FIREFOX_FINGERPRINT_FILE", str(fingerprint))
    monkeypatch.setenv("FIREFOX_EXECUTABLE", "/usr/lib/firefox/firefox")
    monkeypatch.setenv("DISPLAY", ":99")
    calls: dict = {}

    def record_xvfb(display: str, geometry: str) -> None:
        calls["display"] = display
        calls["geometry"] = geometry

    monkeypatch.setattr("dusk_publisher.platforms.browser.ensure_xvfb", record_xvfb)
    _install_fake_firefox(monkeypatch, calls)

    session = await _launch_actions_firefox(None, cookie)

    assert calls["display"] == ":99"
    assert calls["geometry"] == "2880x1800"
    assert calls["launch"]["channel"] == "moz-firefox"
    assert calls["launch"]["headless"] is False
    assert calls["launch"]["executable_path"] == "/usr/lib/firefox/firefox"
    assert calls["launch"]["firefox_user_prefs"]["general.useragent.override"] == "Mozilla/5.0 (test)"
    assert "general.platform.override" not in calls["launch"]["firefox_user_prefs"]
    assert calls["launch"]["env"]["TZ"] == "Asia/Shanghai"
    assert "proxy" not in calls["launch"]
    assert calls["context"]["storage_state"] == str(cookie)
    assert calls["context"]["no_viewport"] is True
    assert calls["context"]["timezone_id"] == "Asia/Shanghai"
    assert "proxy" not in calls["context"]
    assert "extra_http_headers" not in calls["context"]
    assert "persistent" not in calls
    assert session.profile_dir is None


@pytest.mark.asyncio
async def test_actions_launch_defaults_xvfb_to_1920x1080(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FIREFOX_FINGERPRINT_FILE", raising=False)
    monkeypatch.setenv("DISPLAY", ":99")
    calls: dict = {}
    monkeypatch.setattr(
        "dusk_publisher.platforms.browser.ensure_xvfb",
        lambda display, geometry: calls.update(display=display, geometry=geometry),
    )
    _install_fake_firefox(monkeypatch, calls)

    await _launch_actions_firefox(None, None)

    assert calls["display"] == ":99"
    assert calls["geometry"] == "1920x1080"
    assert "storage_state" not in calls["context"]
    assert "persistent" not in calls


@pytest.mark.asyncio
async def test_actions_session_does_not_forward_platform_proxy(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    async def fake(settings, storage_state):
        seen["settings"] = settings
        seen["storage_state"] = storage_state
        return "session"

    monkeypatch.setattr("dusk_publisher.platforms.browser._launch_actions_firefox", fake)
    result = await new_session(
        storage_state=Path("cookies.json"),
        headless=True,
        proxy="socks5://warp-proxy:40000",
        identity="actions",
    )
    assert result == "session"
    assert seen["storage_state"] == Path("cookies.json")
    assert seen["settings"] is None


def test_actions_plan_replays_captured_hints_and_account_proxy():
    fingerprint = FirefoxFingerprint(
        accept_language="zh-CN,zh;q=0.9",
        languages="en-US",
        client_hints={"sec-ch-ua": "captured", "sec-ch-ua-mobile": ""},
    )
    launch, context = actions_firefox_launch_plan(
        fingerprint,
        storage_state=None,
        account_proxy={
            "server": "socks5://127.0.0.1:41000",
            "username": "user",
            "password": "proxy-password-value",
        },
    )
    assert launch["proxy"]["server"] == "socks5://127.0.0.1:41000"
    assert "proxy-password-value" not in launch["proxy"]["server"]
    assert launch["firefox_user_prefs"]["intl.accept_languages"] == "zh-CN,zh;q=0.9"
    assert "privacy.resistFingerprinting" not in launch["firefox_user_prefs"]
    assert context["extra_http_headers"] == {"sec-ch-ua": "captured"}
    assert "sec-ch-ua-mobile" not in context["extra_http_headers"]
    assert "proxy" not in context


@pytest.mark.asyncio
async def test_actions_launch_uses_public_account_proxy_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cookie = tmp_path / "state.json"
    cookie.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    secret = "proxy-password-value"
    (tmp_path / "state.proxy.json").write_text(
        json.dumps(
            {
                "scheme": "socks5",
                "host": "203.0.113.10",
                "port": 1080,
                "username": "user",
                "password": secret,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("FIREFOX_FINGERPRINT_FILE", raising=False)
    monkeypatch.setenv("DISPLAY", ":99")
    calls: dict = {}
    monkeypatch.setattr(
        "dusk_publisher.platforms.browser.ensure_xvfb",
        lambda display, geometry: calls.update(display=display, geometry=geometry),
    )

    def refuse_tunnel(*args, **kwargs):
        raise AssertionError("public socks endpoint must not start cloudflared")

    monkeypatch.setattr(subprocess, "Popen", refuse_tunnel)
    _install_fake_firefox(monkeypatch, calls)

    await _launch_actions_firefox(None, cookie)

    proxy = calls["launch"]["proxy"]
    assert proxy["server"] == "socks5://203.0.113.10:1080"
    assert proxy["username"] == "user"
    assert secret not in proxy["server"]
    assert "proxy" not in calls["context"]
    assert "extra_http_headers" not in calls["context"]
