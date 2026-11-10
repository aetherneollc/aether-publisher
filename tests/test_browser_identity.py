"""Desktop Chrome identity: profile copy, system proxy, and mode selection."""

from __future__ import annotations

import json

import pytest

from dusk_publisher.browser_identity import (
    SystemProxy,
    chromium_proxy_server,
    describe_identity,
    firefox_profile_from_ini,
    normalize_bypass,
    profile_directory,
    redact_proxy,
    resolve_browser_identity,
    resolve_daily_family,
    sync_daily_profile,
    sync_firefox_profile,
)
from dusk_publisher.errors import NeedsLoginError
from dusk_publisher.platforms.browser import BrowserSession
from dusk_publisher.platforms.youtube.adapter import YouTubeAdapter
from dusk_publisher.settings import get_settings


def test_auto_on_a_desktop_with_chrome_uses_the_daily_profile():
    assert (
        resolve_browser_identity(
            "auto",
            cdp_url=None,
            ci=False,
            docker=False,
            pytest_running=False,
            chrome_profile_exists=True,
        )
        == "daily"
    )


def test_auto_in_ci_docker_and_pytest_stays_ephemeral():
    for kwargs in (
        {"ci": True, "docker": False, "pytest_running": False},
        {"ci": False, "docker": True, "pytest_running": False},
        {"ci": False, "docker": False, "pytest_running": True},
    ):
        assert (
            resolve_browser_identity(
                "auto",
                cdp_url="http://127.0.0.1:9222",
                chrome_profile_exists=True,
                **kwargs,
            )
            == "ephemeral"
        )


def test_explicit_cdp_and_ephemeral_are_not_rewritten():
    assert (
        resolve_browser_identity(
            "cdp",
            cdp_url=None,
            ci=True,
            docker=True,
            pytest_running=True,
            chrome_profile_exists=False,
        )
        == "cdp"
    )
    assert (
        resolve_browser_identity(
            "ephemeral",
            cdp_url=None,
            ci=False,
            docker=False,
            pytest_running=False,
            chrome_profile_exists=True,
        )
        == "ephemeral"
    )


def test_pytest_process_never_selects_the_desktop_profile(monkeypatch):
    monkeypatch.setenv("BROWSER_IDENTITY", "daily")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_browser_identity.py::test")
    assert get_settings().browser_mode() == "ephemeral"
    assert get_settings().uses_real_browser() is False


def test_actions_stays_actions_when_ci_and_remote_firefox_are_set():
    assert (
        resolve_browser_identity(
            "actions",
            cdp_url=None,
            ci=True,
            docker=False,
            pytest_running=False,
            chrome_profile_exists=False,
            firefox_profile_exists=False,
            firefox_remote_url="wss://firefox.example/ws",
        )
        == "actions"
    )


def test_actions_still_requires_a_cookie_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_IDENTITY", "actions")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FIREFOX_REMOTE_URL", "wss://firefox.example/ws")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    adapter = YouTubeAdapter(get_settings())
    with pytest.raises(NeedsLoginError):
        adapter._require_cookie("missing")


def test_actions_identity_survives_github_actions_env(monkeypatch):
    monkeypatch.setenv("BROWSER_IDENTITY", "actions")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("FIREFOX_REMOTE_URL", "wss://firefox.example/ws")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_browser_identity.py::test")
    settings = get_settings()
    assert settings.browser_mode() == "actions"
    assert settings.uses_real_browser() is False
    text = describe_identity(
        mode="actions", family="firefox", source=None, profile="Default", proxy=SystemProxy()
    )
    assert "storage_state" in text
    assert "Xvfb" in text
    assert "platform proxies are ignored" in text


def test_proxy_server_formats():
    assert chromium_proxy_server("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert chromium_proxy_server("http://127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert (
        chromium_proxy_server("http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7891")
        == "http://127.0.0.1:7890"
    )
    assert chromium_proxy_server("socks=127.0.0.1:7891") == "socks5://127.0.0.1:7891"
    assert chromium_proxy_server("  ") is None


def test_bypass_and_redaction():
    assert normalize_bypass("localhost;127.0.0.1;<local>") == "localhost,127.0.0.1,<local>"
    assert redact_proxy("http://user:secret@127.0.0.1:7890") == "http://***@127.0.0.1:7890"
    text = describe_identity(
        mode="daily", family="firefox", source=None, profile="Default", proxy=SystemProxy()
    )
    assert "browser=firefox" in text
    assert "system_proxy=direct" in text
    assert "installed Firefox" in text
    assert "platform proxies are ignored" in text
    assert "host default route" in text


def test_profile_directory_reads_last_used(tmp_path):
    (tmp_path / "Local State").write_text(
        json.dumps({"profile": {"last_used": "Profile 1"}}),
        encoding="utf-8",
    )
    assert profile_directory(tmp_path, None) == "Profile 1"
    assert profile_directory(tmp_path, "Default") == "Default"


def test_sync_copies_fingerprint_files_and_skips_passwords(tmp_path):
    source = tmp_path / "User Data"
    profile = source / "Default"
    profile.mkdir(parents=True)
    (source / "Local State").write_text("{}", encoding="utf-8")
    (profile / "Preferences").write_text('{"intl":{"accept_languages":"zh-CN"}}', encoding="utf-8")
    (profile / "Cookies").write_bytes(b"cookies")
    (profile / "Login Data").write_bytes(b"passwords")
    (profile / "History").write_bytes(b"history")
    extension = profile / "Extensions" / "ext"
    extension.mkdir(parents=True)
    (extension / "manifest.json").write_text("{}", encoding="utf-8")

    copied = sync_daily_profile(source, tmp_path / "daily-chrome", "Default")

    dest = tmp_path / "daily-chrome" / "Default"
    assert (dest / "Preferences").read_text(encoding="utf-8").startswith("{")
    assert (dest / "Cookies").read_bytes() == b"cookies"
    assert (dest / "Extensions" / "ext" / "manifest.json").is_file()
    assert not (dest / "Login Data").exists()
    assert not (dest / "History").exists()
    assert "Default/Preferences" in copied
    assert "Local State" in copied


def test_auto_prefers_firefox_when_both_browsers_exist():
    assert (
        resolve_daily_family("auto", firefox_profile_exists=True, chrome_profile_exists=True) == "firefox"
    )
    assert (
        resolve_browser_identity(
            "auto",
            cdp_url=None,
            ci=False,
            docker=False,
            pytest_running=False,
            chrome_profile_exists=False,
            firefox_profile_exists=True,
        )
        == "daily"
    )
    assert resolve_daily_family("chrome", firefox_profile_exists=True, chrome_profile_exists=True) == "chrome"


def test_actions_drives_the_remote_firefox_instead_of_launching_one():
    assert (
        resolve_browser_identity(
            "auto",
            cdp_url=None,
            ci=True,
            docker=True,
            pytest_running=False,
            chrome_profile_exists=False,
            firefox_profile_exists=False,
            firefox_remote_url="wss://firefox.example/ws",
        )
        == "remote-firefox"
    )
    assert (
        resolve_browser_identity(
            "auto",
            cdp_url=None,
            ci=True,
            docker=True,
            pytest_running=True,
            chrome_profile_exists=False,
            firefox_profile_exists=False,
            firefox_remote_url="wss://firefox.example/ws",
        )
        == "ephemeral"
    )


def test_firefox_ini_uses_the_install_default(tmp_path):
    profile = tmp_path / "Profiles" / "abc.default-release"
    profile.mkdir(parents=True)
    ini = tmp_path / "profiles.ini"
    ini.write_text(
        "\n".join(
            [
                "[Install1234]",
                "Default=Profiles/abc.default-release",
                "",
                "[Profile0]",
                "Name=other",
                "IsRelative=1",
                "Path=Profiles/other",
                "",
                "[Profile1]",
                "Name=daily",
                "IsRelative=1",
                "Path=Profiles/abc.default-release",
                "Default=1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert firefox_profile_from_ini(ini) == profile


def test_sync_firefox_profile_skips_passwords_and_history(tmp_path):
    source = tmp_path / "profile"
    source.mkdir()
    (source / "prefs.js").write_text('user_pref("intl.accept_languages", "zh-CN,zh");\n', encoding="utf-8")
    (source / "cookies.sqlite").write_bytes(b"cookies")
    (source / "logins.json").write_text("[]", encoding="utf-8")
    (source / "places.sqlite").write_bytes(b"history")
    (source / "parent.lock").write_bytes(b"lock")
    extension = source / "extensions"
    extension.mkdir()
    (extension / "addon.xpi").write_bytes(b"xpi")
    cache = source / "storage" / "cache2"
    cache.mkdir(parents=True)
    (cache / "junk").write_bytes(b"cache")
    (source / "storage" / "default").mkdir()
    (source / "storage" / "default" / "site").write_bytes(b"ls")

    copied = sync_firefox_profile(source, tmp_path / "daily-firefox")
    dest = tmp_path / "daily-firefox"
    assert (dest / "prefs.js").is_file()
    assert (dest / "cookies.sqlite").read_bytes() == b"cookies"
    assert (dest / "extensions" / "addon.xpi").is_file()
    assert (dest / "storage" / "default" / "site").read_bytes() == b"ls"
    assert not (dest / "logins.json").exists()
    assert not (dest / "places.sqlite").exists()
    assert not (dest / "parent.lock").exists()
    assert not (dest / "storage" / "cache2").exists()
    assert "prefs.js" in copied


def test_sync_missing_profile_names_the_directory(tmp_path):
    (tmp_path / "User Data").mkdir()
    with pytest.raises(FileNotFoundError, match="CHROME_PROFILE_DIRECTORY"):
        sync_daily_profile(tmp_path / "User Data", tmp_path / "out", "Profile 9")


@pytest.mark.asyncio
async def test_attached_session_does_not_quit_the_browser(tmp_path):
    class Fake:
        def __init__(self):
            self.closed = False
            self.stopped = False

        async def close(self):
            self.closed = True

        async def stop(self):
            self.stopped = True

    browser = Fake()
    page = Fake()
    playwright = Fake()
    session = BrowserSession(playwright, browser, Fake(), page, attached=True, export_cookies=False)
    await session.close()
    assert browser.closed is False
    assert page.closed is True
    assert playwright.stopped is True

    dest = tmp_path / "cookies.json"
    label = await session.save_storage_state(dest)
    assert label == "daily-browser-profile"
    assert not dest.exists()
