"""Queued vault checks. No live vault, proxy, or browser."""

from __future__ import annotations

import json

import httpx
import pytest

from dusk_publisher.accounts.checks import classify_cookie_result, execute_check, run_queued_checks
from dusk_publisher.accounts.vault import runner_result_body
from dusk_publisher.models import CheckResult
from dusk_publisher.settings import get_settings


def test_cookie_classification_keeps_network_failures_out_of_cookie_invalid():
    assert classify_cookie_result(CheckResult(ok=True, detail="cookie valid"))[0] == "cookie-valid"
    stale = classify_cookie_result(
        CheckResult(ok=False, detail="redirected to Google sign-in, cookie is stale")
    )
    assert stale[0] == "cookie-invalid"
    likely = classify_cookie_result(
        CheckResult(ok=False, detail="upload widget not found at https://www.tiktok.com/login; cookie likely stale")
    )
    assert likely[0] == "cookie-invalid"
    failed = classify_cookie_result(
        CheckResult(ok=False, detail="check failed: NS_ERROR_PROXY_CONNECTION_REFUSED")
    )
    assert failed[0] == "error"
    unexpected = classify_cookie_result(
        CheckResult(ok=False, detail="unexpected URL after opening Studio: https://studio.youtube.com/")
    )
    assert unexpected[0] == "error"
    missing = classify_cookie_result(CheckResult(ok=False, detail="no cookie file, run login"))
    assert missing[0] == "error"


def test_runner_result_body_matches_the_live_route():
    assert runner_result_body("proxy-up", "proxy accepted a handshake") == {
        "status": "done",
        "result": "up",
        "detail": "proxy accepted a handshake",
    }
    assert runner_result_body("proxy-down", "proxy handshake failed") == {
        "status": "done",
        "result": "down",
        "detail": "proxy handshake failed",
    }
    assert runner_result_body("cookie-valid", "cookie valid")["result"] == "ready"
    invalid = runner_result_body("cookie-invalid", "redirected to login, cookie is stale")
    assert invalid["status"] == "done"
    assert invalid["result"] == "invalid"
    failed = runner_result_body("error", "proxy unreachable; cookie was not checked")
    assert failed == {
        "status": "failed",
        "result": "skipped",
        "detail": "proxy unreachable; cookie was not checked",
    }
    assert "invalid" not in failed.values()


class _Vault:
    def __init__(self, body: dict):
        self.body = body

    async def get_account_state(self, account_id: str) -> dict:
        return self.body


@pytest.mark.asyncio
async def test_down_proxy_does_not_run_the_cookie_check(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    called: list[str] = []

    def proxy_down(proxy):
        return "proxy-down", "proxy handshake failed"

    async def cookie_check(settings, platform, name):
        called.append(name)
        return "cookie-invalid", "should not run"

    monkeypatch.setattr("dusk_publisher.accounts.checks.run_proxy_check", proxy_down)
    monkeypatch.setattr("dusk_publisher.accounts.checks.run_cookie_check", cookie_check)
    outcome, detail = await execute_check(
        get_settings(),
        _Vault(
            {
                "account": {"platform": "youtube", "name": "main"},
                "state": {"cookies": [], "origins": []},
                "proxy": {
                    "scheme": "socks5",
                    "host": "203.0.113.10",
                    "port": 1080,
                    "username": "user",
                    "password": "proxy-password-value",
                },
            }
        ),
        {"id": "c1", "kind": "cookie", "account_id": "a1", "platform": "youtube", "name": "main"},
    )
    assert outcome == "error"
    assert "cookie was not checked" in detail
    assert called == []
    assert "proxy-password-value" not in detail
    assert not (tmp_path / "accounts" / "youtube" / "main.json").exists()


@pytest.mark.asyncio
async def test_run_queued_checks_posts_outcomes(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AETHER_VAULT_URL", "https://vault.example")
    monkeypatch.setenv("AETHER_VAULT_RUNNER_TOKEN", "runner-token")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "access-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "access-secret")
    posted: list[dict] = []
    claims: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/runner/fingerprint":
            return httpx.Response(200, json={"fingerprint": None})
        if path == "/api/runner/checks" and request.method == "GET":
            assert request.url.params["status"] == "queued"
            return httpx.Response(
                200,
                json={
                    "checks": [
                        {"id": "proxy-1", "kind": "proxy", "account_id": "a1", "platform": "youtube", "name": "main"},
                        {"id": "taken", "kind": "cookie", "account_id": "a1", "platform": "youtube", "name": "main"},
                        {"id": "cookie-1", "kind": "cookie", "account_id": "a1", "platform": "youtube", "name": "main"},
                    ]
                },
            )
        if path.endswith("/claim"):
            check_id = path.split("/")[-2]
            claims.append(check_id)
            if check_id == "taken":
                return httpx.Response(409)
            return httpx.Response(200, json={})
        if path.endswith("/state"):
            return httpx.Response(
                200,
                json={
                    "account": {"id": "a1", "platform": "youtube", "name": "main"},
                    "state": {"cookies": [], "origins": []},
                    "proxy": {
                        "scheme": "http",
                        "host": "203.0.113.10",
                        "port": 8080,
                        "username": "user",
                        "password": "proxy-password-value",
                    },
                },
            )
        if path.endswith("/result"):
            posted.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={})
        return httpx.Response(404)

    original_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("dusk_publisher.accounts.vault.httpx.AsyncClient", factory)
    monkeypatch.setattr(
        "dusk_publisher.accounts.checks.run_proxy_check",
        lambda proxy: ("proxy-up", "proxy accepted a handshake"),
    )

    async def cookie_check(settings, platform, name):
        return "cookie-invalid", "redirected to login, cookie is stale"

    monkeypatch.setattr("dusk_publisher.accounts.checks.run_cookie_check", cookie_check)
    results = await run_queued_checks(get_settings())
    assert results == [("proxy-1", "proxy-up"), ("cookie-1", "cookie-invalid")]
    assert claims == ["proxy-1", "taken", "cookie-1"]
    assert posted == [
        {"status": "done", "result": "up", "detail": "proxy accepted a handshake"},
        {"status": "done", "result": "invalid", "detail": "redirected to login, cookie is stale"},
    ]
    assert all("outcome" not in body for body in posted)
    rendered = json.dumps(posted)
    assert "proxy-password-value" not in rendered
    assert "access-secret" not in rendered
