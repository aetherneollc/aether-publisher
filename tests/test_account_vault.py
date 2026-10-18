from __future__ import annotations

import httpx
import pytest

from dusk_publisher.accounts.vault import AccountVaultClient


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
