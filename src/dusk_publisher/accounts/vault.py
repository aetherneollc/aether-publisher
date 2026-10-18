"""Fetch encrypted-at-rest browser state from Aether Account Vault."""

from __future__ import annotations

import json
from pathlib import Path

import httpx


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
            state = state_response.json()["state"]
            destination = accounts_dir / account["platform"] / f"{account['name']}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(state), encoding="utf-8")
            destination.chmod(0o600)
            synced += 1
        return synced
