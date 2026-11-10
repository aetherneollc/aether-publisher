"""Claim queued vault proxy and cookie checks and post one outcome each.

Cookie checks open Firefox through that account's proxy and reuse the
adapter login-url check. A proxy or network failure stays ``error`` and is
posted as status ``failed`` / result ``skipped``, not cookie ``invalid``.
"""

from __future__ import annotations

import json
from typing import Any

from dusk_publisher.accounts.proxy import (
    parse_account_proxy,
    public_detail,
    run_proxy_check,
    write_proxy_sidecar,
)
from dusk_publisher.accounts.vault import AccountVaultClient
from dusk_publisher.logging_setup import get_logger
from dusk_publisher.models import CheckResult
from dusk_publisher.platforms.registry import get_adapter
from dusk_publisher.settings import Settings

log = get_logger(__name__)


def classify_cookie_result(result: CheckResult) -> tuple[str, str]:
    """Map an adapter login check onto a vault outcome.

    ``cookie-invalid`` is only a finished page load that showed a signed-out
    session. Navigation failures, missing files, and ambiguous URLs stay
    ``error`` so a down proxy does not mark the cookie invalid.
    """
    detail = public_detail(result.detail or "")
    if result.ok:
        return "cookie-valid", detail or "cookie valid"
    lowered = detail.lower()
    if lowered.startswith("check failed") or "no cookie file" in lowered or "unexpected url" in lowered:
        return "error", detail or "cookie check did not finish"
    if "cookie is stale" in lowered or "cookie likely stale" in lowered:
        return "cookie-invalid", detail
    return "error", detail or "cookie check did not finish"


async def run_queued_checks(settings: Settings) -> list[tuple[str, str]]:
    """Claim every queued check, run it, and post the result. Returns id, outcome."""
    if not settings.account_vault_url or not settings.account_vault_runner_token:
        raise RuntimeError("AETHER_VAULT_URL and AETHER_VAULT_RUNNER_TOKEN are required")
    vault = AccountVaultClient(
        settings.account_vault_url,
        settings.account_vault_runner_token,
        cf_access_client_id=settings.cf_access_client_id,
        cf_access_client_secret=settings.cf_access_client_secret,
    )
    posted: list[tuple[str, str]] = []
    try:
        try:
            written = await vault.sync_firefox_fingerprint(settings.data_dir)
            log.info("vault fingerprint %s", "written" if written else "unchanged")
        except Exception as exc:
            log.warning("fingerprint sync failed: %s", public_detail(str(exc)))
        for item in await vault.list_queued_checks():
            check_id = str(item.get("id") or "").strip()
            if not check_id:
                continue
            if not await vault.claim_check(check_id):
                log.info("check %s was not claimed", check_id)
                continue
            outcome, detail = await execute_check(settings, vault, item)
            await vault.post_check_result(check_id, outcome, detail)
            posted.append((check_id, outcome))
            log.info("check %s outcome %s", check_id, outcome)
    finally:
        await vault.aclose()
    return posted


async def execute_check(settings: Settings, vault: AccountVaultClient, item: dict[str, Any]) -> tuple[str, str]:
    kind = str(item.get("kind") or item.get("type") or "").strip().lower()
    account_id = str(item.get("account_id") or "").strip()
    try:
        if not account_id:
            return "error", "check is missing account_id"
        body = await vault.get_account_state(account_id)
        account = body.get("account") if isinstance(body.get("account"), dict) else {}
        platform = str(item.get("platform") or account.get("platform") or "").strip().lower()
        name = str(item.get("name") or item.get("account_name") or account.get("name") or "").strip()
        if (
            not platform
            or not name
            or platform in {".", ".."}
            or name in {".", ".."}
            or "/" in platform
            or "/" in name
            or "\\" in platform
            or "\\" in name
        ):
            return "error", "check is missing platform or account name"
        proxy = parse_account_proxy(body.get("proxy"))
        if kind == "proxy":
            return run_proxy_check(proxy)
        if kind == "cookie":
            state = body.get("state")
            if not isinstance(state, dict):
                return "error", "account has no saved browser state"
            if proxy is not None:
                proxy_outcome, proxy_detail = run_proxy_check(proxy)
                if proxy_outcome != "proxy-up":
                    if proxy_outcome == "proxy-down":
                        return "error", "proxy unreachable; cookie was not checked"
                    return "error", proxy_detail
            _write_account(settings, platform, name, state, body.get("proxy"))
            return await run_cookie_check(settings, platform, name)
        return "error", "unknown check kind"
    except Exception as exc:
        return "error", public_detail(str(exc))


async def run_cookie_check(settings: Settings, platform: str, name: str) -> tuple[str, str]:
    adapter = get_adapter(platform, settings)
    try:
        result = await adapter.check(name)
    except Exception as exc:
        return "error", public_detail(str(exc))
    return classify_cookie_result(result)


def _write_account(settings: Settings, platform: str, name: str, state: dict[str, Any], proxy_payload: Any) -> None:
    destination = settings.accounts_dir / platform / f"{name}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(state), encoding="utf-8")
    destination.chmod(0o600)
    write_proxy_sidecar(destination, parse_account_proxy(proxy_payload))
