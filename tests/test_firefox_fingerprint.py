"""Fingerprint prefs and the Firefox launch options that ignore platform proxies."""

from __future__ import annotations

from pathlib import Path

import pytest

from dusk_publisher.firefox_fingerprint import (
    firefox_extra_http_headers,
    firefox_launch_kwargs,
    firefox_user_prefs,
    load_firefox_fingerprint,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_example_file_is_only_placeholders():
    example = Path(__file__).resolve().parents[1] / "config" / "firefox-fingerprint.example.yaml"
    fp = load_firefox_fingerprint(path=example)
    assert fp is not None
    assert firefox_user_prefs(fp) == {}
    assert firefox_launch_kwargs(fp, platform_proxy="socks5://warp-proxy:40000") == {}
    assert fp.fields_to_fill() == [
        "user_agent",
        "platform",
        "oscpu",
        "languages",
        "timezone",
        "webgl_vendor",
        "webgl_renderer",
        "screen_width",
        "screen_height",
        "device_pixel_ratio",
        "max_touch_points",
    ]


def test_build_prefs_from_a_filled_file(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        """
user_agent: "Mozilla/5.0 (test)"
platform: "Win32"
oscpu: "Windows NT 10.0; Win64; x64"
languages: ["zh-CN", "zh", "en-US"]
timezone: "Asia/Shanghai"
screen_width: 1920
screen_height: 1080
device_pixel_ratio: 1.25
max_touch_points: 0
webgl_vendor: "Google Inc. (NVIDIA)"
webgl_renderer: "ANGLE (NVIDIA, NVIDIA GeForce TEST Direct3D11 vs_5_0 ps_5_0)"
""",
    )
    fp = load_firefox_fingerprint(path=path)
    assert fp is not None
    prefs = firefox_user_prefs(fp)
    assert prefs["general.useragent.override"] == "Mozilla/5.0 (test)"
    assert prefs["general.platform.override"] == "Win32"
    assert prefs["general.oscpu.override"] == "Windows NT 10.0; Win64; x64"
    assert prefs["intl.accept_languages"] == "zh-CN,zh,en-US"
    assert prefs["webgl.override-unmasked-vendor"] == "Google Inc. (NVIDIA)"
    assert prefs["webgl.override-unmasked-renderer"].startswith("ANGLE (NVIDIA")
    assert prefs["webgl.sanitize-unmasked-renderer"] is False
    assert prefs["layout.css.devPixelsPerPx"] == 1.25
    assert "privacy.resistFingerprinting" not in prefs
    assert "screen_width" not in prefs
    assert all("touch" not in name.lower() for name in prefs)
    assert fp.screen_width == 1920
    assert fp.screen_height == 1080
    assert fp.max_touch_points == 0
    assert fp.fields_to_fill() == []


def test_empty_placeholders_do_not_set_overrides(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        """
user_agent: ""
platform: ""
oscpu: ""
languages: ""
timezone: ""
screen_width: null
screen_height: null
device_pixel_ratio: null
max_touch_points: null
webgl_vendor: ""
webgl_renderer: ""
""",
    )
    fp = load_firefox_fingerprint(path=path)
    assert fp is not None
    assert firefox_user_prefs(fp) == {}
    assert fp.fields_to_fill() == [
        "user_agent",
        "platform",
        "oscpu",
        "languages",
        "timezone",
        "webgl_vendor",
        "webgl_renderer",
        "screen_width",
        "screen_height",
        "device_pixel_ratio",
        "max_touch_points",
    ]


def test_vendor_without_renderer_does_not_disable_sanitizing(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        'webgl_vendor: "NVIDIA Corporation"\n',
    )
    prefs = firefox_user_prefs(load_firefox_fingerprint(path=path))
    assert prefs == {"webgl.override-unmasked-vendor": "NVIDIA Corporation"}


def test_launch_sets_process_timezone_and_drops_platform_proxy(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        """
user_agent: "Mozilla/5.0 (test)"
timezone: "Asia/Shanghai"
""",
    )
    fp = load_firefox_fingerprint(path=path)
    kwargs = firefox_launch_kwargs(
        fp,
        platform_proxy="socks5://warp-proxy:40000",
        base_env={"DISPLAY": ":1", "PATH": "/usr/bin"},
    )
    assert "proxy" not in kwargs
    assert kwargs["timezone_id"] == "Asia/Shanghai"
    assert kwargs["env"]["TZ"] == "Asia/Shanghai"
    assert kwargs["env"]["DISPLAY"] == ":1"
    assert kwargs["firefox_user_prefs"] == {"general.useragent.override": "Mozilla/5.0 (test)"}
    assert "privacy.resistFingerprinting" not in kwargs["firefox_user_prefs"]


def test_account_proxy_is_applied_and_platform_env_proxy_is_not():
    kwargs = firefox_launch_kwargs(
        None,
        platform_proxy="socks5://warp-proxy:40000",
        account_proxy={
            "server": "socks5://127.0.0.1:41000",
            "username": "user",
            "password": "proxy-password-value",
        },
    )
    assert kwargs["proxy"]["server"] == "socks5://127.0.0.1:41000"
    assert "warp-proxy" not in kwargs["proxy"]["server"]
    assert "proxy-password-value" not in kwargs["proxy"]["server"]
    assert "extra_http_headers" not in kwargs
    assert "privacy.resistFingerprinting" not in kwargs


def test_captured_accept_language_and_client_hints_are_replayed(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        """
languages: ["en-US"]
accept_language: "zh-CN,zh;q=0.9,en;q=0.8"
client_hints:
  sec-ch-ua: "captured-ua"
  sec-ch-ua-mobile: ""
  sec-ch-ua-platform: "Windows"
  not-a-hint: "do-not-send"
""",
    )
    fp = load_firefox_fingerprint(path=path)
    assert fp is not None
    prefs = firefox_user_prefs(fp)
    assert prefs["intl.accept_languages"] == "zh-CN,zh;q=0.9,en;q=0.8"
    headers = firefox_extra_http_headers(fp)
    assert headers == {"sec-ch-ua": "captured-ua", "sec-ch-ua-platform": "Windows"}
    assert "sec-ch-ua-mobile" not in headers
    assert "not-a-hint" not in headers
    assert "Chrome" not in str(headers)
    kwargs = firefox_launch_kwargs(fp, platform_proxy="socks5://warp-proxy:40000")
    assert kwargs["extra_http_headers"] == headers
    assert "proxy" not in kwargs
    assert "privacy.resistFingerprinting" not in prefs


def test_request_headers_replay_only_non_empty_sec_ch_ua(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        """
languages: ["en-US"]
architecture: "x86"
bitness: "64"
model: ""
platform_version: "15.0.0"
ua_full_version: "131.0.0.0"
ua_mobile: false
wow64: false
full_version_list:
  - brand: Chromium
    version: "131.0.0.0"
accept_language: "should-not-win"
client_hints:
  sec-ch-ua-arch: "should-not-send"
request_headers:
  user-agent: "Mozilla/5.0 (header)"
  accept-language: "zh-CN,zh;q=0.9,en;q=0.8"
  sec-ch-ua: "captured-ua"
  sec-ch-ua-mobile: ""
  sec-ch-ua-platform: "Windows"
  not-a-hint: "do-not-send"
""",
    )
    fp = load_firefox_fingerprint(path=path)
    assert fp is not None
    assert fp.architecture == "x86"
    assert fp.bitness == "64"
    assert fp.platform_version == "15.0.0"
    assert fp.ua_full_version == "131.0.0.0"
    assert fp.full_version_list == (("Chromium", "131.0.0.0"),)
    assert fp.ua_mobile is False
    assert fp.wow64 is False
    prefs = firefox_user_prefs(fp)
    assert prefs["intl.accept_languages"] == "zh-CN,zh;q=0.9,en;q=0.8"
    headers = firefox_extra_http_headers(fp)
    assert headers == {"sec-ch-ua": "captured-ua", "sec-ch-ua-platform": "Windows"}
    assert "user-agent" not in headers
    assert "accept-language" not in headers
    assert "sec-ch-ua-mobile" not in headers
    assert "sec-ch-ua-arch" not in headers
    assert "not-a-hint" not in headers
    assert "Chromium" not in str(headers)
    assert "x86" not in str(headers)
    kwargs = firefox_launch_kwargs(fp)
    assert kwargs["extra_http_headers"] == headers
    assert "privacy.resistFingerprinting" not in prefs


def test_empty_request_headers_do_not_invent_hints(tmp_path: Path):
    path = _write(
        tmp_path / "firefox-fingerprint.yaml",
        """
languages: "en-US"
architecture: "x86"
ua_full_version: "131.0.0.0"
full_version_list:
  - brand: Chromium
    version: "131.0.0.0"
client_hints:
  sec-ch-ua: "should-not-send"
request_headers:
  accept-language: ""
  user-agent: "Mozilla/5.0 (header)"
  sec-ch-ua: ""
  sec-ch-ua-arch: ""
  sec-ch-ua-full-version-list: ""
""",
    )
    fp = load_firefox_fingerprint(path=path)
    assert fp is not None
    assert firefox_user_prefs(fp)["intl.accept_languages"] == "en-US"
    assert firefox_extra_http_headers(fp) == {}
    assert "extra_http_headers" not in firefox_launch_kwargs(fp)


def test_empty_client_hints_add_no_headers(tmp_path: Path):
    path = _write(tmp_path / "firefox-fingerprint.yaml", "client_hints: {}\naccept_language: \"\"\n")
    fp = load_firefox_fingerprint(path=path)
    assert fp is not None
    assert firefox_extra_http_headers(fp) == {}
    assert "extra_http_headers" not in firefox_launch_kwargs(fp)
    assert firefox_user_prefs(fp) == {}
    assert "privacy.resistFingerprinting" not in firefox_user_prefs(fp)


def test_env_file_is_the_only_candidate_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    missing = tmp_path / "missing.yaml"
    other = _write(tmp_path / "firefox-fingerprint.yaml", 'user_agent: "from-data-dir"\n')
    monkeypatch.setenv("FIREFOX_FINGERPRINT_FILE", str(missing))
    assert load_firefox_fingerprint(data_dir=tmp_path) is None
    monkeypatch.setenv("FIREFOX_FINGERPRINT_FILE", str(other))
    loaded = load_firefox_fingerprint(data_dir=tmp_path)
    assert loaded is not None
    assert loaded.user_agent == "from-data-dir"


def test_data_dir_file_is_found_without_the_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FIREFOX_FINGERPRINT_FILE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _write(tmp_path / "firefox-fingerprint.yaml", 'platform: "Win32"\n')
    loaded = load_firefox_fingerprint(data_dir=tmp_path)
    assert loaded is not None
    assert loaded.platform == "Win32"


def test_pytest_does_not_pick_up_a_developer_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FIREFOX_FINGERPRINT_FILE", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_firefox_fingerprint.py::test")
    _write(tmp_path / "firefox-fingerprint.yaml", 'platform: "Win32"\n')
    assert load_firefox_fingerprint(data_dir=tmp_path) is None
