from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import yaml

from dusk_publisher.accounts.vault import FINGERPRINT_YAML_FIELDS, AccountVaultClient
from dusk_publisher.firefox_fingerprint import firefox_user_prefs, load_firefox_fingerprint


@pytest.mark.asyncio
async def test_syncs_only_ready_accounts_and_sends_both_auth_layers(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/runner/accounts":
            return httpx.Response(200, json={"accounts": [
                {"id": "a1", "platform": "youtube", "name": "main", "status": "ready"},
                {"id": "a2", "platform": "x", "name": "later", "status": "empty"},
            ]})
        return httpx.Response(200, json={"state": {"cookies": [{"name": "sid"}], "origins": []}})

    vault = AccountVaultClient(
        "https://vault.example",
        "runner-token",
        cf_access_client_id="access-id",
        cf_access_client_secret="access-secret",
    )
    await vault._client.aclose()
    vault._client = httpx.AsyncClient(
        headers=vault._client.headers,
        transport=httpx.MockTransport(handler),
    )

    assert await vault.sync_ready_accounts(tmp_path) == 1
    assert (tmp_path / "youtube" / "main.json").exists()
    assert not (tmp_path / "x" / "later.json").exists()
    assert seen[0].headers["authorization"] == "Bearer runner-token"
    assert seen[0].headers["cf-access-client-id"] == "access-id"
    assert seen[0].headers["cf-access-client-secret"] == "access-secret"
    await vault.aclose()


async def _vault(handler) -> AccountVaultClient:
    vault = AccountVaultClient(
        "https://vault.example",
        "runner-token",
        cf_access_client_id="access-id",
        cf_access_client_secret="access-secret",
    )
    headers = vault._client.headers
    await vault._client.aclose()
    vault._client = httpx.AsyncClient(headers=headers, transport=httpx.MockTransport(handler))
    return vault


@pytest.mark.asyncio
async def test_fingerprint_yaml_round_trips_and_skips_empty_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fingerprint = {
        "user_agent": "Mozilla/5.0 (test)",
        "platform": "",
        "oscpu": "",
        "languages": "",
        "timezone": "",
        "screen_width": 1920,
        "screen_height": 1080,
        "device_pixel_ratio": 1.25,
        "max_touch_points": 0,
        "webgl_vendor": "",
        "webgl_renderer": "",
        "ignored": "not a fingerprint field",
    }
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"fingerprint": fingerprint})

    modes: list[int] = []
    real_chmod = os.chmod

    def spy_chmod(path, mode):
        modes.append(mode)
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", spy_chmod)
    vault = await _vault(handler)
    try:
        assert await vault.sync_firefox_fingerprint(tmp_path) is True
    finally:
        await vault.aclose()

    destination = tmp_path / "firefox-fingerprint.yaml"
    document = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert list(document) == list(FINGERPRINT_YAML_FIELDS)
    assert "ignored" not in document
    assert document["user_agent"] == "Mozilla/5.0 (test)"
    assert document["platform"] == ""
    assert document["screen_width"] == 1920
    assert document["device_pixel_ratio"] == 1.25
    assert document["max_touch_points"] == 0
    loaded = load_firefox_fingerprint(path=destination)
    assert loaded is not None
    assert loaded.architecture == ""
    assert loaded.full_version_list == ()
    assert loaded.ua_mobile is None
    from dusk_publisher.firefox_fingerprint import firefox_extra_http_headers

    assert firefox_extra_http_headers(loaded) == {}
    prefs = firefox_user_prefs(loaded)
    assert prefs["general.useragent.override"] == "Mozilla/5.0 (test)"
    assert prefs["layout.css.devPixelsPerPx"] == 1.25
    assert "general.platform.override" not in prefs
    assert "general.oscpu.override" not in prefs
    assert "intl.accept_languages" not in prefs
    assert "webgl.override-unmasked-vendor" not in prefs
    assert "webgl.override-unmasked-renderer" not in prefs
    assert all("touch" not in name.lower() for name in prefs)
    assert loaded.max_touch_points == 0
    assert seen[0].url.path == "/api/runner/fingerprint"
    assert seen[0].headers["authorization"] == "Bearer runner-token"
    assert seen[0].headers["cf-access-client-id"] == "access-id"
    assert seen[0].headers["cf-access-client-secret"] == "access-secret"
    assert 0o600 in modes


@pytest.mark.asyncio
async def test_null_fingerprint_does_not_overwrite_existing_yaml(tmp_path: Path):
    destination = tmp_path / "firefox-fingerprint.yaml"
    destination.write_text("user_agent: keep-me\n", encoding="utf-8")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"fingerprint": None})

    vault = await _vault(handler)
    try:
        assert await vault.sync_firefox_fingerprint(tmp_path) is False
    finally:
        await vault.aclose()

    assert destination.read_text(encoding="utf-8") == "user_agent: keep-me\n"
    assert seen[0].url.path == "/api/runner/fingerprint"
    assert seen[0].headers["authorization"] == "Bearer runner-token"


@pytest.mark.asyncio
async def test_sync_writes_proxy_sidecar_and_redacts_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
    import logging

    secret = "proxy-password-value"
    caplog.set_level(logging.INFO)

    def handler(request):
        if request.url.path == "/api/runner/accounts":
            return httpx.Response(200, json={"accounts": [
                {"id": "a1", "platform": "youtube", "name": "main", "status": "ready"},
            ]})
        return httpx.Response(200, json={
            "account": {"id": "a1", "platform": "youtube", "name": "main"},
            "state": {"cookies": [], "origins": []},
            "proxy": {
                "scheme": "socks5",
                "host": "socks.internal.example",
                "port": 1080,
                "username": "user",
                "password": secret,
            },
        })

    modes: list[int] = []
    real_chmod = os.chmod

    def spy_chmod(path, mode, **kwargs):
        modes.append(mode)
        return real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", spy_chmod)
    vault = await _vault(handler)
    try:
        assert await vault.sync_ready_accounts(tmp_path) == 1
    finally:
        await vault.aclose()

    sidecar = tmp_path / "youtube" / "main.proxy.json"
    stored = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert stored["scheme"] == "socks5"
    assert stored["host"] == "socks.internal.example"
    assert stored["port"] == 1080
    assert stored["username"] == "user"
    assert stored["password"] == secret
    assert secret not in caplog.text
    assert "socks5://***@socks.internal.example:1080" in caplog.text
    assert 0o600 in modes


@pytest.mark.asyncio
async def test_null_proxy_removes_sidecar(tmp_path: Path):
    sidecar = tmp_path / "youtube" / "main.proxy.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('{"scheme":"socks5","host":"old.example","port":1080,"username":"","password":""}\n', encoding="utf-8")

    def handler(request):
        if request.url.path == "/api/runner/accounts":
            return httpx.Response(200, json={"accounts": [
                {"id": "a1", "platform": "youtube", "name": "main", "status": "ready"},
            ]})
        return httpx.Response(200, json={"state": {"cookies": [], "origins": []}, "proxy": None})

    vault = await _vault(handler)
    try:
        assert await vault.sync_ready_accounts(tmp_path) == 1
    finally:
        await vault.aclose()
    assert not sidecar.exists()


@pytest.mark.asyncio
async def test_fingerprint_reads_live_runner_headers(tmp_path: Path):
    fingerprint = {
        "user_agent": "Mozilla/5.0 (test)",
        "languages": "en-US",
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "platform_version": "15.0.0",
        "ua_full_version": "131.0.0.0",
        "full_version_list": [{"brand": "Chromium", "version": "131.0.0.0"}],
        "ua_mobile": False,
        "wow64": False,
        "request_headers": {
            "user-agent": "Mozilla/5.0 (header)",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "sec-ch-ua": "captured-ua",
            "sec-ch-ua-mobile": "",
            "sec-ch-ua-platform": "",
            "sec-ch-ua-platform-version": "",
            "sec-ch-ua-arch": "",
            "sec-ch-ua-bitness": "",
            "sec-ch-ua-model": "",
            "sec-ch-ua-full-version-list": "",
            "sec-ch-ua-wow64": "",
            "not-a-hint": "leave-in-file",
        },
        "accept_language": "should-not-win",
        "client_hints": {"sec-ch-ua-arch": "should-not-send"},
        "ignored": "not a fingerprint field",
    }

    def handler(request):
        return httpx.Response(200, json={"fingerprint": fingerprint})

    vault = await _vault(handler)
    try:
        assert await vault.sync_firefox_fingerprint(tmp_path) is True
    finally:
        await vault.aclose()

    document = yaml.safe_load((tmp_path / "firefox-fingerprint.yaml").read_text(encoding="utf-8"))
    assert list(document) == list(FINGERPRINT_YAML_FIELDS)
    assert "ignored" not in document
    assert "accept_language" not in document
    assert "client_hints" not in document
    assert document["request_headers"]["accept-language"] == "zh-CN,zh;q=0.9,en;q=0.8"
    assert document["request_headers"]["sec-ch-ua"] == "captured-ua"
    assert document["request_headers"]["sec-ch-ua-mobile"] == ""
    assert document["architecture"] == "x86"
    loaded = load_firefox_fingerprint(path=tmp_path / "firefox-fingerprint.yaml")
    assert loaded is not None
    assert loaded.architecture == "x86"
    assert loaded.bitness == "64"
    assert loaded.model == ""
    assert loaded.platform_version == "15.0.0"
    assert loaded.ua_full_version == "131.0.0.0"
    assert loaded.full_version_list == (("Chromium", "131.0.0.0"),)
    assert loaded.ua_mobile is False
    assert loaded.wow64 is False
    from dusk_publisher.firefox_fingerprint import firefox_extra_http_headers

    prefs = firefox_user_prefs(loaded)
    headers = firefox_extra_http_headers(loaded)
    assert prefs["intl.accept_languages"] == "zh-CN,zh;q=0.9,en;q=0.8"
    assert headers == {"sec-ch-ua": "captured-ua"}
    assert "user-agent" not in headers
    assert "accept-language" not in headers
    assert "sec-ch-ua-arch" not in headers
    assert "sec-ch-ua-mobile" not in headers
    assert "not-a-hint" not in headers
    assert "Chromium" not in str(headers)
    assert "privacy.resistFingerprinting" not in prefs
