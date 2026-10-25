#!/usr/bin/env python3
"""Smoke-test GET {AETHER_VAULT_URL}/api/runner/accounts.

The GitHub Actions workflow calls this script. It prints the HTTP status,
content type, and the first 200 bytes of the body when the call fails, and it
redacts the vault URL, runner bearer token, and Cloudflare Access service
token before writing anything. A non-JSON body (including the Access login
redirect, which is HTTP 302 HTML) fails with an explicit message.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
from urllib.parse import urlsplit

SECRET_ENV = (
    "AETHER_VAULT_URL",
    "AETHER_VAULT_RUNNER_TOKEN",
    "CF_ACCESS_CLIENT_ID",
    "CF_ACCESS_CLIENT_SECRET",
)
BODY_PREVIEW_BYTES = 200
MAX_BODY_BYTES = 256_000

ACCESS_HINT = (
    "A redirect with an HTML body is the Cloudflare Access login page. "
    "The vault Access application needs a Service Auth policy that includes "
    "this runner's service token. A human Allow policy does not accept "
    "CF-Access-Client-Id and CF-Access-Client-Secret. Also set the Worker "
    "secret RUNNER_API_TOKEN to the same value as AETHER_VAULT_RUNNER_TOKEN."
)
TOKEN_HINT = (
    "The Worker rejected the bearer token. Set the aether-account-vault "
    "secret RUNNER_API_TOKEN to the same value as AETHER_VAULT_RUNNER_TOKEN."
)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if len(secret) >= 6:
            text = text.replace(secret, "[redacted]")
    return text


def redact_bytes(body: bytes, secrets: list[str]) -> bytes:
    redacted = body
    for secret in secrets:
        if len(secret) >= 6:
            redacted = redacted.replace(secret.encode("utf-8"), b"[redacted]")
    return redacted


def preview_body(body: bytes, secrets: list[str]) -> str:
    snippet = redact_bytes(body, secrets)[:BODY_PREVIEW_BYTES]
    return snippet.decode("utf-8", errors="replace")


def vault_origin(raw: str) -> str:
    """Return scheme://host[:port] or raise ValueError. Never echoes `raw`."""
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("AETHER_VAULT_URL must be an absolute http(s) URL.")
    if parts.username or parts.password:
        raise ValueError("AETHER_VAULT_URL must not include userinfo.")
    if parts.query or parts.fragment or parts.path not in {"", "/"}:
        path = parts.path or "/"
        raise ValueError(
            "AETHER_VAULT_URL must be the vault origin only, with no path. "
            f"It includes {path!r}. The smoke test requests /api/runner/accounts itself."
        )
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def failure_report(
    status: int,
    content_type: str,
    body: bytes,
    secrets: list[str],
) -> str | None:
    """Return a diagnostic report, or None when the body is a JSON accounts array."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    has_accounts = isinstance(payload, dict) and isinstance(payload.get("accounts"), list)
    if payload is not None and 200 <= status < 300 and has_accounts:
        return None

    if payload is None:
        reason = "response is not JSON"
    elif not (200 <= status < 300):
        reason = f"HTTP {status} from /api/runner/accounts"
    else:
        reason = "response JSON does not contain an accounts array"

    content_type_text = content_type.strip() or "(none)"
    lines = [
        f"Account vault smoke test failed: {reason}.",
        f"HTTP status: {status}",
        f"Content-Type: {content_type_text}",
        f"Body (first 200 bytes): {preview_body(body, secrets)!r}",
    ]
    if payload is None and status in {301, 302, 303, 307, 308}:
        lines.append(ACCESS_HINT)
    elif (
        status == 401
        and isinstance(payload, dict)
        and payload.get("error") == "unauthorized"
    ):
        lines.append(TOKEN_HINT)
    return redact("\n".join(lines) + "\n", secrets)


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    missing = [name for name in SECRET_ENV if not os.environ.get(name)]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"missing or empty: {joined}.")
    for name in SECRET_ENV:
        value = os.environ[name]
        if any(char in value for char in "\r\n\x00"):
            raise ValueError(f"{name} contains a newline or NUL.")
        values[name] = value
    return values


def fetch_accounts(origin: str, token: str, access_id: str, access_secret: str) -> tuple[int, str, bytes]:
    parts = urlsplit(origin)
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname or "",
            parts.port or 443,
            timeout=30,
        )
    else:
        connection = http.client.HTTPConnection(
            parts.hostname or "",
            parts.port or 80,
            timeout=30,
        )
    try:
        connection.request(
            "GET",
            "/api/runner/accounts",
            headers={
                "Authorization": f"Bearer {token}",
                "CF-Access-Client-Id": access_id,
                "CF-Access-Client-Secret": access_secret,
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        content_type = response.getheader("Content-Type") or ""
        body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            body = body[:MAX_BODY_BYTES]
        return response.status, content_type, body
    finally:
        connection.close()


def run() -> int:
    try:
        values = _read_env()
        origin = vault_origin(values["AETHER_VAULT_URL"])
    except ValueError as exc:
        print(f"Account vault smoke test failed: {exc}", file=sys.stderr)
        return 1

    secrets = [values[name] for name in SECRET_ENV]
    try:
        status, content_type, body = fetch_accounts(
            origin,
            values["AETHER_VAULT_RUNNER_TOKEN"],
            values["CF_ACCESS_CLIENT_ID"],
            values["CF_ACCESS_CLIENT_SECRET"],
        )
    except Exception as exc:
        message = redact(
            "Account vault smoke test failed: request to /api/runner/accounts "
            f"did not complete ({type(exc).__name__}: {exc}).",
            secrets,
        )
        print(message, file=sys.stderr)
        return 1

    report = failure_report(status, content_type, body, secrets)
    if report is not None:
        print(report, file=sys.stderr, end="")
        return 1
    print("Account vault smoke test passed.")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        secrets = [os.environ.get(name, "") for name in SECRET_ENV]
        print(
            redact(
                f"Account vault smoke test failed: {type(exc).__name__}: {exc}",
                secrets,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
