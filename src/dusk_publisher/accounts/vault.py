"""Fetch encrypted-at-rest browser state from Aether Account Vault."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import httpx
import yaml

from dusk_publisher.accounts.proxy import (
    parse_account_proxy,
    redact_account_proxy,
    write_proxy_sidecar,
)
from dusk_publisher.logging_setup import get_logger

log = get_logger(__name__)

# Keys GET /api/runner/fingerprint returns. Missing client hints stay empty
# strings inside request_headers. This writer does not invent Chrome hints.
FINGERPRINT_YAML_FIELDS = (
    "user_agent",
    "platform",
    "oscpu",
    "languages",
    "timezone",
    "screen_width",
    "screen_height",
    "device_pixel_ratio",
    "max_touch_points",
    "webgl_vendor",
    "webgl_renderer",
    "architecture",
    "bitness",
    "model",
    "platform_version",
    "ua_full_version",
    "full_version_list",
    "ua_mobile",
    "wow64",
    "request_headers",
)

# Internal runner labels. post_check_result maps them onto the live body
# {status, result, detail}. A proxy or network failure stays "error" here so
# it is posted as failed/skipped, never as cookie result "invalid".
CHECK_OUTCOMES = ("proxy-up", "proxy-down", "cookie-valid", "cookie-invalid", "error")
RUNNER_RESULT_BY_OUTCOME = {
    "proxy-up": ("done", "up"),
    "proxy-down": ("done", "down"),
    "cookie-valid": ("done", "ready"),
    "cookie-invalid": ("done", "invalid"),
    "error": ("failed", "skipped"),
}


class AccountVaultClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        cf_access_client_id: str | None = None,
        cf_access_client_secret: str | None = None,
        timeout: float = 30.0,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        if cf_access_client_id and cf_access_client_secret:
            headers["CF-Access-Client-Id"] = cf_access_client_id
            headers["CF-Access-Client-Secret"] = cf_access_client_secret
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def sync_ready_accounts(self, accounts_dir: Path) -> int:
        response = await self._client.get(f"{self.base_url}/api/runner/accounts")
        response.raise_for_status()
        accounts = response.json().get("accounts", [])
        synced = 0
        for account in accounts:
            if account.get("status") != "ready":
                continue
            state_response = await self._client.get(
                f"{self.base_url}/api/runner/accounts/{account['id']}/state"
            )
            state_response.raise_for_status()
            body = state_response.json()
            if not isinstance(body, dict) or "state" not in body:
                raise ValueError("vault account state must include state")
            destination = accounts_dir / account["platform"] / f"{account['name']}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(body["state"]), encoding="utf-8")
            destination.chmod(0o600)
            proxy = parse_account_proxy(body.get("proxy"))
            write_proxy_sidecar(destination, proxy)
            label = "none" if proxy is None else redact_account_proxy(proxy)
            log.info("synced %s/%s proxy %s", account["platform"], account["name"], label)
            synced += 1
        return synced

    async def get_account_state(self, account_id: str) -> dict[str, Any]:
        """GET /api/runner/accounts/:id/state → {account, state, proxy}.

        ``proxy`` is null or ``{scheme, host, port, username, password}``.
        The default host is a private Zero Trust hostname, not a public IP.
        """
        response = await self._client.get(f"{self.base_url}/api/runner/accounts/{account_id}/state")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("vault account state must be an object")
        return body

    async def list_queued_checks(self) -> list[dict[str, Any]]:
        """GET /api/runner/checks?status=queued.

        Each item is ``{id, kind, account_id, platform, name}`` where ``kind``
        is ``proxy`` or ``cookie``.
        """
        response = await self._client.get(
            f"{self.base_url}/api/runner/checks",
            params={"status": "queued"},
        )
        response.raise_for_status()
        body = response.json()
        checks = body.get("checks") if isinstance(body, dict) else None
        if not isinstance(checks, list):
            raise ValueError("vault checks response must include a checks list")
        return [item for item in checks if isinstance(item, dict)]

    async def claim_check(self, check_id: str) -> bool:
        """POST /api/runner/checks/:id/claim. False when another runner took it."""
        response = await self._client.post(f"{self.base_url}/api/runner/checks/{check_id}/claim")
        if response.status_code in {404, 409}:
            return False
        response.raise_for_status()
        return True

    async def post_check_result(self, check_id: str, outcome: str, detail: str) -> None:
        """POST /api/runner/checks/:id/result with status, result, and detail."""
        response = await self._client.post(
            f"{self.base_url}/api/runner/checks/{check_id}/result",
            json=runner_result_body(outcome, detail),
        )
        response.raise_for_status()

    async def sync_firefox_fingerprint(self, data_dir: Path) -> bool:
        """Write ``$DATA_DIR/firefox-fingerprint.yaml``.

        A null fingerprint leaves an existing file untouched. The written
        names are the runner fingerprint keys, including ``request_headers``.
        """
        response = await self._client.get(f"{self.base_url}/api/runner/fingerprint")
        response.raise_for_status()
        body = response.json()
        fingerprint = body.get("fingerprint") if isinstance(body, dict) else None
        if fingerprint is None:
            return False
        if not isinstance(fingerprint, dict):
            raise ValueError("vault fingerprint must be an object or null")
        destination = Path(data_dir) / "firefox-fingerprint.yaml"
        _write_private_text(destination, _fingerprint_yaml(fingerprint))
        return True


def runner_result_body(outcome: str, detail: str) -> dict[str, str]:
    """Body accepted by POST /api/runner/checks/:id/result.

    Proxy checks use result ``up`` or ``down``. A finished cookie check uses
    ``ready`` or ``invalid``. A check that did not finish uses status
    ``failed`` and result ``skipped``, which does not change account status.
    """
    try:
        status, result = RUNNER_RESULT_BY_OUTCOME[outcome]
    except KeyError as exc:
        raise ValueError(f"unsupported check outcome {outcome}") from exc
    return {"status": status, "result": result, "detail": detail}


def _fingerprint_yaml(payload: Mapping[str, Any]) -> str:
    document = {key: payload.get(key) for key in FINGERPRINT_YAML_FIELDS}
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
