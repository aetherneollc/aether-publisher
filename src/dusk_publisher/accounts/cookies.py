"""Cookie file path convention, shared by every platform adapter.

Per the shared contract: `/data/accounts/<platform>/<account>.json`. The
JSON content itself is whatever Playwright/patchright's
`context.storage_state(path=...)` writes (cookies + localStorage), so it is
opaque to us — adapters are the only code that ever reads its *contents*.
"""

from __future__ import annotations

import json
from pathlib import Path


def cookie_path(data_dir: Path, platform: str, account: str) -> Path:
    return Path(data_dir) / "accounts" / platform / f"{account}.json"


def has_valid_cookie_file(path: Path) -> bool:
    """Cheap, browser-less sanity check: file exists, is non-empty JSON, and
    looks like a Playwright storage_state (has a `cookies` key).

    This is intentionally *not* a check that the cookie is still accepted by
    the platform (that requires a browser round-trip — see each adapter's
    `check()`). It exists so `publish()` can fail fast with `NeedsLoginError`
    before ever importing patchright or opening a network connection, which
    is what lets the local-queue pipeline be exercised end-to-end without a
    browser installed.
    """
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return False
    return isinstance(data, dict) and "cookies" in data
