"""Exception types shared across dusk-publisher.

Design goal (explicitly requested): when a browser-automation step fails, the
error must say *which step* and *which selector* did not resolve, because
platform UIs drift and selectors go stale. Every adapter step should be
wrapped so failures surface as `StepFailedError`, never a bare Playwright
`TimeoutError`.
"""

from __future__ import annotations


class DuskPublisherError(Exception):
    """Base class for all dusk-publisher errors."""


class NeedsLoginError(DuskPublisherError):
    """Raised when an account has no cookie file, or the cookie is stale.

    Deliberately raised *before* touching the browser/network for `publish`,
    so operators (and automated tests) get a fast, clear signal instead of a
    browser launching just to fail on a login redirect.
    """

    def __init__(self, platform: str, account: str, reason: str = "no valid cookie file"):
        self.platform = platform
        self.account = account
        self.reason = reason
        super().__init__(
            f"[{platform}:{account}] needs login ({reason}). "
            f"Run: dusk-publisher {platform} login --account {account}"
        )


class StepFailedError(DuskPublisherError):
    """A specific browser-automation step failed.

    Always carries `platform`, `step` (human-readable stage name, e.g.
    'fill title'), and, when applicable, the `selector` that could not be
    found/clicked/filled — so operators know exactly what to go re-inspect
    in devtools when the platform changes its UI.
    """

    def __init__(self, platform: str, step: str, selector: str | None = None, cause: Exception | str | None = None):
        self.platform = platform
        self.step = step
        self.selector = selector
        self.cause = cause
        parts = [f"[{platform}] step '{step}' failed"]
        if selector:
            parts.append(f"(selector: {selector})")
        if cause:
            parts.append(f": {cause}")
        super().__init__(" ".join(parts))


class AccountNotFoundError(DuskPublisherError):
    """No account could be routed for (platform, lang) and no generic fallback exists."""

    def __init__(self, platform: str, lang: str | None):
        self.platform = platform
        self.lang = lang
        super().__init__(
            f"No account configured for platform={platform!r} lang={lang!r}, "
            f"and no generic (lang-less) fallback account exists for {platform!r}. "
            f"Add one to accounts.yaml."
        )


class AssetNotFoundError(DuskPublisherError):
    """Local media file (video/cover) could not be resolved or downloaded."""


class ControlPlaneError(DuskPublisherError):
    """Non-2xx or malformed response from the 暮色工房 control plane /ops/* API."""

    def __init__(self, endpoint: str, status_code: int | None, body: str = ""):
        self.endpoint = endpoint
        self.status_code = status_code
        self.body = body
        super().__init__(f"control plane {endpoint} failed (status={status_code}): {body[:300]}")


class QueueFormatError(DuskPublisherError):
    """Local queue YAML/JSON file is malformed or fails schema validation."""
