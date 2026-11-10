"""Optional Firefox fingerprint applied when the publisher launches Firefox.

The values are copied from the operator's own Firefox. This module does not
ship measured strings. Empty fields are omitted so a placeholder file cannot
override the browser with a blank user agent.

Prefs that current Firefox actually reads (mozilla-firefox/firefox main):

- ``general.useragent.override``, ``general.platform.override``,
  ``general.oscpu.override`` (dom/base/Navigator.cpp). Ignored when
  ``privacy.resistFingerprinting`` is on, so this module never sets that pref.
- ``intl.accept_languages`` (intl/locale/LocaleService.cpp), only when it is a
  user value. Playwright's ``firefox_user_prefs`` are user prefs.
- ``webgl.override-unmasked-vendor`` and ``webgl.override-unmasked-renderer``
  (modules/libpref/init/StaticPrefList.yaml, read in
  dom/canvas/ClientWebGLContext.cpp).
- ``webgl.sanitize-unmasked-renderer`` defaults to true and rewrites the
  renderer through SanitizeRenderer (for example an NVIDIA string becomes a
  bucket plus ", or similar"). It is set to false only when a renderer string
  is applied, so the override is the string that was configured.
- ``layout.css.devPixelsPerPx`` is the device-pixel ratio. Values <= 0 mean
  "use the system", so a missing ratio is not written.

``window.screen`` and ``navigator.maxTouchPoints`` have no preference.
On the Actions runner the virtual screen is sized from ``screen_width`` and
``screen_height`` (times ``device_pixel_ratio`` when that is greater than 0).
Touch points come from the widget; baseline fingerprinting collapses any
count above 1 to 5. Those fields are kept in the file and are not written
as prefs.

Timezone is the Firefox process ``TZ`` plus Playwright's ``timezone_id``,
which Firefox Juggler applies with ``Browser.setTimezoneOverride``. It is not
a preference.

``request_headers`` is the header map from the vault runner. A non-empty
``accept-language`` there is the ``intl.accept_languages`` pref, including
``q`` values. Only non-empty ``sec-ch-ua*`` names are Playwright
``extra_http_headers`` (``extraHTTPHeaders``). Empty strings, ``user-agent``,
and any other header are omitted. ``architecture``, ``bitness``, ``model``,
``platform_version``, ``ua_full_version``, ``full_version_list``,
``ua_mobile``, and ``wow64`` are kept and are not turned into client hints.
This module never invents a Chrome client-hint set. A hand-written file may
still set ``accept_language`` and ``client_hints`` when ``request_headers``
does not carry those values. ``privacy.resistFingerprinting`` is never set.

``account_proxy`` on the launch is the account sidecar after any local
Zero Trust forward. ``platform_proxy`` remains ignored so ``YT_PROXY``,
``X_PROXY``, and ``TIKTOK_PROXY`` stay unused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

# Never written. Firefox skips the overrides above while this is true.
_RESIST_FINGERPRINTING = "privacy.resistFingerprinting"

# Headers a capture may include. Absent names are not invented.
CLIENT_HINT_HEADERS = (
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-platform-version",
    "sec-ch-ua-arch",
    "sec-ch-ua-bitness",
    "sec-ch-ua-model",
    "sec-ch-ua-full-version-list",
    "sec-ch-ua-wow64",
)


@dataclass(frozen=True)
class FirefoxFingerprint:
    user_agent: str = ""
    platform: str = ""
    oscpu: str = ""
    languages: str = ""
    timezone: str = ""
    screen_width: int | None = None
    screen_height: int | None = None
    device_pixel_ratio: float | None = None
    max_touch_points: int | None = None
    webgl_vendor: str = ""
    webgl_renderer: str = ""
    accept_language: str = ""
    client_hints: dict[str, str] = field(default_factory=dict)
    architecture: str = ""
    bitness: str = ""
    model: str = ""
    platform_version: str = ""
    ua_full_version: str = ""
    full_version_list: tuple[tuple[str, str], ...] = ()
    ua_mobile: bool | None = None
    wow64: bool | None = None
    source: Path | None = None

    def fields_to_fill(self) -> list[str]:
        """Configured names that are still empty placeholders."""
        missing: list[str] = []
        for name in ("user_agent", "platform", "oscpu", "languages", "timezone", "webgl_vendor", "webgl_renderer"):
            if not getattr(self, name):
                missing.append(name)
        for name in ("screen_width", "screen_height", "device_pixel_ratio", "max_touch_points"):
            if getattr(self, name) is None:
                missing.append(name)
        return missing


def fingerprint_search_paths(data_dir: Path | None = None) -> list[Path]:
    """Where a local fingerprint file is looked up.

    ``FIREFOX_FINGERPRINT_FILE`` wins and is not combined with the defaults.
    Otherwise: ``$DATA_DIR/firefox-fingerprint.yaml``, then
    ``./config/firefox-fingerprint.yaml``, then the file next to this repo.
    The example file is never one of these paths.
    """
    explicit = os.environ.get("FIREFOX_FINGERPRINT_FILE", "").strip()
    if explicit:
        return [Path(explicit)]
    paths: list[Path] = []
    if data_dir is not None:
        paths.append(Path(data_dir) / "firefox-fingerprint.yaml")
    cwd_config = Path.cwd() / "config" / "firefox-fingerprint.yaml"
    paths.append(cwd_config)
    repo_config = Path(__file__).resolve().parents[2] / "config" / "firefox-fingerprint.yaml"
    if repo_config != cwd_config:
        paths.append(repo_config)
    return paths


def load_firefox_fingerprint(
    data_dir: Path | None = None,
    *,
    path: Path | None = None,
) -> FirefoxFingerprint | None:
    """Load the fingerprint file when one is present.

    Pytest does not search the developer machine unless ``path`` or
    ``FIREFOX_FINGERPRINT_FILE`` is set for that test. A missing file is not
    an error: the browser then keeps Firefox's own values.
    """
    if path is None and os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("FIREFOX_FINGERPRINT_FILE"):
        return None
    candidates = [path] if path is not None else fingerprint_search_paths(data_dir)
    chosen = next((item for item in candidates if item is not None and item.is_file()), None)
    if chosen is None:
        return None
    payload = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{chosen} must be a mapping of fingerprint fields")
    return FirefoxFingerprint(
        user_agent=_text(payload.get("user_agent")),
        platform=_text(payload.get("platform")),
        oscpu=_text(payload.get("oscpu")),
        languages=_languages(payload.get("languages")),
        timezone=_text(payload.get("timezone")),
        screen_width=_optional_int(payload.get("screen_width"), "screen_width"),
        screen_height=_optional_int(payload.get("screen_height"), "screen_height"),
        device_pixel_ratio=_optional_float(payload.get("device_pixel_ratio"), "device_pixel_ratio"),
        max_touch_points=_optional_int(payload.get("max_touch_points"), "max_touch_points"),
        webgl_vendor=_text(payload.get("webgl_vendor")),
        webgl_renderer=_text(payload.get("webgl_renderer")),
        accept_language=_accept_language(payload),
        client_hints=_client_hints_from_payload(payload),
        architecture=_text(payload.get("architecture")),
        bitness=_text(payload.get("bitness")),
        model=_text(payload.get("model")),
        platform_version=_text(payload.get("platform_version")),
        ua_full_version=_text(payload.get("ua_full_version")),
        full_version_list=_full_version_list(payload.get("full_version_list")),
        ua_mobile=_optional_bool(payload.get("ua_mobile"), "ua_mobile"),
        wow64=_optional_bool(payload.get("wow64"), "wow64"),
        source=chosen,
    )


def firefox_user_prefs(fingerprint: FirefoxFingerprint | None) -> dict[str, Any]:
    """User prefs Playwright passes to Firefox. Empty fields are skipped.

    Does not set ``privacy.resistFingerprinting``. Does not invent prefs for
    screen size or maxTouchPoints.
    """
    if fingerprint is None:
        return {}
    prefs: dict[str, Any] = {}
    if fingerprint.user_agent:
        prefs["general.useragent.override"] = fingerprint.user_agent
    if fingerprint.platform:
        prefs["general.platform.override"] = fingerprint.platform
    if fingerprint.oscpu:
        prefs["general.oscpu.override"] = fingerprint.oscpu
    language_pref = fingerprint.accept_language or fingerprint.languages
    if language_pref:
        prefs["intl.accept_languages"] = language_pref
    if fingerprint.webgl_vendor:
        prefs["webgl.override-unmasked-vendor"] = fingerprint.webgl_vendor
    if fingerprint.webgl_renderer:
        prefs["webgl.override-unmasked-renderer"] = fingerprint.webgl_renderer
        # Default true rewrites the override. False keeps the configured string.
        prefs["webgl.sanitize-unmasked-renderer"] = False
    if fingerprint.device_pixel_ratio is not None and fingerprint.device_pixel_ratio > 0:
        prefs["layout.css.devPixelsPerPx"] = float(fingerprint.device_pixel_ratio)
    prefs.pop(_RESIST_FINGERPRINTING, None)
    return prefs


def firefox_process_env(
    fingerprint: FirefoxFingerprint | None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Environment for the Firefox process. Sets ``TZ`` when a zone is configured."""
    if fingerprint is None or not fingerprint.timezone:
        return None
    env = dict(os.environ if base_env is None else base_env)
    env["TZ"] = fingerprint.timezone
    return env


def firefox_extra_http_headers(fingerprint: FirefoxFingerprint | None) -> dict[str, str]:
    """Captured ``sec-ch-ua*`` values for Playwright ``extra_http_headers``.

    Empty and missing hints are omitted. ``user-agent``, ``accept-language``,
    and unknown names are omitted. User-agent data fields are not converted
    into hints. Nothing is filled in with a Chrome default.
    """
    if fingerprint is None:
        return {}
    headers: dict[str, str] = {}
    for name in CLIENT_HINT_HEADERS:
        value = fingerprint.client_hints.get(name, "")
        if isinstance(value, str) and value.strip():
            headers[name] = value.strip()
    return headers


def firefox_launch_kwargs(
    fingerprint: FirefoxFingerprint | None,
    *,
    platform_proxy: str | None = None,
    account_proxy: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Launch options merged into a Firefox launch.

    ``platform_proxy`` is ignored so ``YT_PROXY`` / ``X_PROXY`` /
    ``TIKTOK_PROXY`` stay unused. ``account_proxy`` is a Playwright proxy
    dict for this account (public endpoint, or the local Zero Trust forward).
    """
    del platform_proxy
    kwargs: dict[str, Any] = {}
    prefs = firefox_user_prefs(fingerprint)
    if prefs:
        kwargs["firefox_user_prefs"] = prefs
    env = firefox_process_env(fingerprint, base_env)
    if env is not None:
        kwargs["env"] = env
    if fingerprint is not None and fingerprint.timezone:
        kwargs["timezone_id"] = fingerprint.timezone
    headers = firefox_extra_http_headers(fingerprint)
    if headers:
        kwargs["extra_http_headers"] = headers
    if account_proxy:
        kwargs["proxy"] = dict(account_proxy)
    return kwargs


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"expected a string, got {type(value).__name__}")
    return value.strip()


def _languages(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_text(item) for item in value]
        return ",".join(part for part in parts if part)
    raise ValueError("languages must be a string or a list of strings")


def _request_header_map(value: Any) -> dict[str, str] | None:
    """Normalized ``request_headers``, or None when that capture is absent.

    Only string values are kept. Empty strings stay so a present-but-empty
    ``sec-ch-ua*`` key can suppress a hand-written ``client_hints`` fallback.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("request_headers must be a mapping")
    headers: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str):
            continue
        headers[str(key).strip().lower()] = item.strip()
    return headers


def _accept_language(payload: Mapping[str, Any]) -> str:
    """Prefer a non-empty Accept-Language from ``request_headers``."""
    headers = _request_header_map(payload["request_headers"]) if "request_headers" in payload else None
    if headers is not None:
        captured = headers.get("accept-language", "")
        if captured:
            return captured
    return _text(payload.get("accept_language"))


def _client_hints_from_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    """Non-empty ``sec-ch-ua*`` headers. Do not invent the rest.

    When ``request_headers`` includes any ``sec-ch-ua*`` key, those values
    are the capture. Otherwise a hand-written ``client_hints`` map is used.
    """
    headers = _request_header_map(payload["request_headers"]) if "request_headers" in payload else None
    if headers is not None and any(name in headers for name in CLIENT_HINT_HEADERS):
        return {name: headers[name] for name in CLIENT_HINT_HEADERS if headers.get(name)}
    return _client_hints(payload.get("client_hints"))


def _full_version_list(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, list):
        raise ValueError("full_version_list must be a list")
    items: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("full_version_list entries must be objects")
        brand = item.get("brand")
        version = item.get("version")
        if not isinstance(brand, str) or not isinstance(version, str):
            raise ValueError("full_version_list entries need brand and version strings")
        items.append((brand, version))
    return tuple(items)


def _optional_bool(value: Any, name: str) -> bool | None:
    if value is None or value == "":
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _client_hints(value: Any) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError("client_hints must be a mapping")
    hints: dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip().lower()
        if name not in CLIENT_HINT_HEADERS or not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            hints[name] = text
    return hints


def _optional_int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_float(value: Any, name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)
