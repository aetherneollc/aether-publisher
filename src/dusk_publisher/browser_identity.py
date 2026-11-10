"""Use this machine's real browser profile and the same network route as daily browsing.

A fresh Playwright context has its own viewport, an empty profile, and — when
a platform proxy is set — a different public IP. Daily mode copies the
identity-bearing parts of the installed Firefox profile (preferred) or Chrome
profile into a separate directory and launches that installed browser against
the copy. Hardware fingerprint comes from this machine. Language, window
placement, extensions, and cookies come from the copied profile. The exit IP
follows the OS route, including the Windows system proxy when one is enabled,
and never the per-platform proxy used by the CI WARP sidecar.

Chrome 136+ refuses remote debugging of its default user-data directory, and
Firefox cannot share one profile with a second process, so the publisher
always launches the copy rather than the live profile directory.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Files that change language, window size, extension set, and login cookies.
# Passwords (Login Data) and History stay in the real profile.
_PROFILE_FILES = (
    "Preferences",
    "Secure Preferences",
    "Cookies",
    "Cookies-wal",
    "Cookies-shm",
    "Extension Cookies",
    "Extension Cookies-wal",
    "Extension Cookies-shm",
    "Network/Cookies",
    "Network/Cookies-wal",
    "Network/Cookies-shm",
)
_PROFILE_DIRS = (
    "Extensions",
    "Local Extension Settings",
    "Extension State",
    "Local Storage",
)
_NEVER_COPY = {
    "Login Data",
    "Login Data-journal",
    "Login Data For Account",
    "Login Data For Account-journal",
    "History",
    "History-journal",
    # Firefox password and history stores. Cookies live in cookies.sqlite.
    "logins.json",
    "logins-backup.json",
    "key4.db",
    "key3.db",
    "places.sqlite",
    "places.sqlite-wal",
    "places.sqlite-shm",
    "formhistory.sqlite",
    "parent.lock",
    "lock",
    ".parentlock",
}
_FIREFOX_FILES = (
    "prefs.js",
    "user.js",
    "cookies.sqlite",
    "cookies.sqlite-wal",
    "cookies.sqlite-shm",
    "extensions.json",
    "extension-preferences.json",
    "extension-settings.json",
    "addonStartup.json.lz4",
    "permissions.sqlite",
    "content-prefs.sqlite",
    "containers.json",
    "handlers.json",
    "xulstore.json",
    "storage.sqlite",
    "webappsstore.sqlite",
)
_FIREFOX_DIRS = (
    "extensions",
    "browser-extension-data",
    "storage",
)
_SKIP_DIR_NAMES = {
    "cache2",
    "startupCache",
    "thumbnails",
    "shader-cache",
    "crashes",
    "datareporting",
    "minidumps",
    "jumpListCache",
}


@dataclass(frozen=True)
class SystemProxy:
    server: str | None = None
    bypass: str | None = None
    pac_url: str | None = None

    @property
    def label(self) -> str:
        if self.server:
            return f"system:{redact_proxy(self.server)}"
        if self.pac_url:
            return f"pac:{self.pac_url}"
        return "direct"


def resolve_browser_identity(
    mode: str | None,
    *,
    cdp_url: str | None,
    ci: bool,
    docker: bool,
    pytest_running: bool,
    chrome_profile_exists: bool,
    firefox_profile_exists: bool = False,
    firefox_remote_url: str | None = None,
) -> str:
    """Return `ephemeral`, `daily`, `cdp`, `remote-firefox`, or `actions`.

    GitHub Actions and Docker do not have the desktop Firefox, so `auto`
    there stays on the cookie-file browser unless `FIREFOX_REMOTE_URL` is
    set. That URL is the running Firefox; the Action drives it and the
    fingerprint and exit IP stay on that browser. A developer machine with
    no remote URL can still launch a local profile copy.

    `actions` launches Firefox on this machine. `CI` / `GITHUB_ACTIONS` do
    not rewrite it to ephemeral Chromium, and `FIREFOX_REMOTE_URL` does not
    switch it to `remote-firefox`.
    """
    selected = (mode or "auto").strip().lower()
    if selected not in {"auto", "ephemeral", "daily", "cdp", "remote-firefox", "actions"}:
        selected = "auto"
    if firefox_remote_url and selected in {"auto", "daily", "remote-firefox"} and not pytest_running:
        return "remote-firefox"
    if selected != "auto":
        return selected
    if pytest_running or ci or docker:
        return "ephemeral"
    if cdp_url:
        return "cdp"
    if firefox_profile_exists or chrome_profile_exists:
        return "daily"
    return "ephemeral"


def resolve_daily_family(
    family: str | None,
    *,
    firefox_profile_exists: bool,
    chrome_profile_exists: bool,
) -> str:
    """Which installed browser daily mode launches.

    `auto` prefers Firefox, because that is the desktop browser this machine
    is used with. `chrome` remains available when Firefox is not installed.
    """
    selected = (family or "auto").strip().lower()
    if selected not in {"auto", "firefox", "chrome"}:
        selected = "auto"
    if selected == "firefox":
        return "firefox"
    if selected == "chrome":
        return "chrome"
    if firefox_profile_exists:
        return "firefox"
    if chrome_profile_exists:
        return "chrome"
    return "firefox"


def chrome_source_user_data() -> Path | None:
    """Directory Chrome itself calls the user-data dir, not a single profile folder."""
    explicit = os.environ.get("CHROME_USER_DATA_DIR")
    if explicit:
        return Path(explicit)
    home = Path.home()
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Google" / "Chrome" / "User Data")
    candidates.extend(
        [
            home / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
            home / "Library" / "Application Support" / "Google" / "Chrome",
            home / ".config" / "google-chrome",
        ]
    )
    for path in candidates:
        if path.is_dir():
            return path
    return None


def firefox_profiles_ini() -> Path | None:
    explicit = os.environ.get("FIREFOX_PROFILES_INI")
    if explicit:
        return Path(explicit)
    home = Path.home()
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Mozilla" / "Firefox" / "profiles.ini")
    candidates.extend(
        [
            home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "profiles.ini",
            home / "Library" / "Application Support" / "Firefox" / "profiles.ini",
            home / ".mozilla" / "firefox" / "profiles.ini",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def firefox_profile_from_ini(ini_path: Path) -> Path | None:
    """Pick the profile Firefox itself opens for the default install."""
    import configparser

    parser = configparser.RawConfigParser()
    parser.optionxform = str
    try:
        parser.read(ini_path, encoding="utf-8-sig")
    except configparser.Error:
        return None
    base = ini_path.parent
    install_default = None
    for section in parser.sections():
        if section.startswith("Install") and parser.has_option(section, "Default"):
            install_default = parser.get(section, "Default").strip()
            break

    chosen: tuple[str, bool] | None = None
    if install_default:
        normalized = install_default.replace("\\", "/")
        for section in parser.sections():
            if not parser.has_option(section, "Path"):
                continue
            path = parser.get(section, "Path").strip()
            if path.replace("\\", "/") == normalized:
                chosen = (path, _ini_flag(parser, section, "IsRelative", default=True))
                break
        if chosen is None:
            chosen = (install_default, not Path(install_default).is_absolute())
    if chosen is None:
        for section in parser.sections():
            if not parser.has_option(section, "Path"):
                continue
            if _ini_flag(parser, section, "Default", default=False):
                chosen = (parser.get(section, "Path").strip(), _ini_flag(parser, section, "IsRelative", default=True))
                break
    if chosen is None:
        return None
    raw, relative = chosen
    profile = base / raw if relative else Path(raw)
    return profile


def firefox_source_profile() -> Path | None:
    """The Firefox profile directory (the folder that contains prefs.js)."""
    explicit = os.environ.get("FIREFOX_PROFILE_PATH")
    if explicit:
        return Path(explicit)
    ini = firefox_profiles_ini()
    if ini is None:
        return None
    return firefox_profile_from_ini(ini)


def _ini_flag(parser, section: str, option: str, *, default: bool) -> bool:
    if not parser.has_option(section, option):
        return default
    return parser.get(section, option).strip() == "1"


def profile_directory(user_data: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    local_state = user_data / "Local State"
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "Default"
    name = payload.get("profile", {}).get("last_used") if isinstance(payload, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "Default"


def chromium_proxy_server(server: str) -> str | None:
    """Turn a Windows ProxyServer value into a Chromium proxy server URL.

    Manual proxies look like `127.0.0.1:7890`. Per-scheme values look like
    `http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7891`. The HTTP
    proxy is what a normal Chrome window uses for HTTPS via CONNECT, so it
    wins over the SOCKS entry.
    """
    text = server.strip()
    if not text:
        return None
    if "=" in text:
        mapping: dict[str, str] = {}
        for part in text.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            mapping[key.strip().lower()] = value.strip()
        for key, scheme in (("https", "http"), ("http", "http"), ("socks", "socks5")):
            value = mapping.get(key)
            if not value:
                continue
            if "://" in value:
                return value
            return f"{scheme}://{value}"
        return None
    if "://" in text:
        return text
    return f"http://{text}"


def normalize_bypass(value: str | None) -> str | None:
    if not value:
        return None
    parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return ",".join(parts) or None


def redact_proxy(server: str) -> str:
    if "@" not in server:
        return server
    scheme, _, rest = server.partition("://")
    host = rest.split("@", 1)[1]
    prefix = f"{scheme}://" if scheme else ""
    return f"{prefix}***@{host}"


def read_system_proxy() -> SystemProxy:
    """Proxy settings a normal desktop Chrome inherits from the OS.

    Chromium started by Playwright does not read the Windows system proxy on
    its own. Without copying it across, the publisher would leave via the raw
    network while the daily browser leaves via the proxy.
    """
    if os.name != "nt":
        return SystemProxy()
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = bool(_reg_dword(key, "ProxyEnable"))
            server = _reg_str(key, "ProxyServer") if enabled else None
            bypass = _reg_str(key, "ProxyOverride") if enabled else None
            pac = _reg_str(key, "AutoConfigURL")
    except OSError:
        return SystemProxy()
    parsed = chromium_proxy_server(server) if server else None
    if parsed:
        return SystemProxy(server=parsed, bypass=normalize_bypass(bypass))
    if pac:
        return SystemProxy(pac_url=pac)
    return SystemProxy()


def _reg_dword(key, name: str) -> int:
    import winreg

    try:
        value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return 0
    if kind != winreg.REG_DWORD:
        return 0
    return int(value)


def _reg_str(key, name: str) -> str | None:
    import winreg

    try:
        value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if kind not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} or not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def sync_firefox_profile(source_profile: Path, dest_profile: Path) -> list[str]:
    """Copy a Firefox profile into a directory the publisher can launch alone.

    The destination is the profile directory itself (`-profile`), not a parent
    user-data folder. Passwords and history stay in the real profile.
    """
    if not source_profile.is_dir():
        raise FileNotFoundError(
            f"Firefox profile directory was not found: {source_profile}. "
            "Set FIREFOX_PROFILE_PATH to the folder about:support calls Profile Directory."
        )
    dest_profile.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in _FIREFOX_FILES:
        if _copy_file(source_profile / relative, dest_profile / relative):
            copied.append(relative)
    for relative in _FIREFOX_DIRS:
        if _copy_tree(source_profile / relative, dest_profile / relative):
            copied.append(f"{relative}/")
    for name in ("parent.lock", "lock", ".parentlock"):
        try:
            (dest_profile / name).unlink()
        except OSError:
            pass
    return copied


def sync_daily_profile(source_user_data: Path, dest_user_data: Path, profile: str) -> list[str]:
    """Copy fingerprint and login state into a non-default user-data directory.

    Chrome 136+ ignores remote debugging on the default directory, and two
    processes cannot share one Cookies database. The copy is what the publisher
    launches. Returns the relative paths that were copied this call.
    """
    source_profile = source_user_data / profile
    if not source_profile.is_dir():
        raise FileNotFoundError(
            f"Chrome profile {profile!r} was not found in {source_user_data}. "
            "Set CHROME_PROFILE_DIRECTORY to the folder name shown in chrome://version."
        )
    dest_user_data.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    local_state = source_user_data / "Local State"
    if _copy_file(local_state, dest_user_data / "Local State"):
        copied.append("Local State")
    dest_profile = dest_user_data / profile
    dest_profile.mkdir(parents=True, exist_ok=True)
    for relative in _PROFILE_FILES:
        src = source_profile / relative
        if src.name in _NEVER_COPY:
            continue
        if _copy_file(src, dest_profile / relative):
            copied.append(f"{profile}/{relative}")
    for relative in _PROFILE_DIRS:
        src = source_profile / relative
        if _copy_tree(src, dest_profile / relative):
            copied.append(f"{profile}/{relative}/")
    _clear_process_locks(dest_user_data, dest_profile)
    return copied


def _copy_file(src: Path, dest: Path) -> bool:
    if not src.is_file() or src.name in _NEVER_COPY:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dest)
    except OSError:
        return False
    return True


def _copy_tree(src: Path, dest: Path) -> bool:
    if not src.is_dir():
        return False
    copied_any = False
    for path in src.rglob("*"):
        relative = path.relative_to(src)
        if not path.is_file() or path.name in _NEVER_COPY:
            continue
        if any(part in _SKIP_DIR_NAMES for part in relative.parts):
            continue
        if _copy_file(path, dest / relative):
            copied_any = True
    return copied_any


def _clear_process_locks(user_data: Path, profile: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"):
        for directory in (user_data, profile):
            try:
                (directory / name).unlink()
            except OSError:
                pass


def describe_identity(
    *,
    mode: str,
    family: str,
    source: Path | None,
    profile: str,
    proxy: SystemProxy,
    firefox_profile: Path | None = None,
) -> str:
    lines = [
        f"mode={mode}",
        f"browser={family}",
        f"system_proxy={proxy.label}",
    ]
    if family == "firefox":
        lines.insert(2, f"firefox_profile={firefox_profile if firefox_profile else '(not found)'}")
    else:
        lines.insert(2, f"chrome_profile={source if source else '(not found)'}")
        lines.insert(3, f"profile_directory={profile}")
    if mode == "daily" and family == "firefox":
        lines.append(
            "launches the installed Firefox with a copy of that profile, headed. "
            "When firefox-fingerprint.yaml is present, its user prefs and process timezone are applied"
        )
        lines.append(
            "platform proxies are ignored so browser egress stays on the host default route"
        )
    elif mode == "daily":
        lines.append(
            "launches the installed Google Chrome with a copy of that profile, "
            "headed, extensions enabled, and no viewport or user-agent override"
        )
        lines.append("platform proxies are ignored so the exit IP stays on this machine's normal route")
    elif mode == "actions":
        lines.append(
            "launches Mozilla Firefox on this machine, headed, on the Xvfb display. "
            "Prefs come from firefox-fingerprint.yaml. Login is the account storage_state"
        )
        lines.append(
            "platform proxies are ignored. An account proxy sidecar is applied when it is next to the cookie file"
        )
    elif mode == "remote-firefox":
        lines.append("drives the Firefox at FIREFOX_REMOTE_URL and does not launch one")
        lines.append("fingerprint and exit IP are that Firefox; WARP and platform proxies are not used")
    elif mode == "cdp":
        lines.append("attaches to the Chrome already listening on BROWSER_CDP_URL and does not close it")
        lines.append("that Chrome's own network stack is the exit IP; platform proxies are ignored")
    else:
        lines.append(
            "ephemeral mode opens a fresh browser. YT_PROXY, X_PROXY, and TIKTOK_PROXY apply only here"
        )
    return "\n".join(lines)
