"""Shared patchright session helpers + step wrappers used by every adapter.

patchright (https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python, MIT)
is a drop-in replacement for Playwright's async API with stealth patches
applied, which is exactly why `social-auto-upload` (see
THIRD_PARTY_NOTICES.md) standardized on it for this kind of long-running,
cookie-based browser automation. We follow the same choice.

All Playwright/patchright imports are deliberately *lazy* (inside functions),
so importing this module — and therefore importing any adapter — never
requires patchright to be installed. It's only required once code actually
tries to open a browser (`login`, `check`, or a `publish` that already
passed the cookie-file gate). That's what keeps the local-queue pipeline
runnable in the "needs login" self-test without patchright/Chromium present.

`daily` / `cdp` identity modes are the exception to the fresh-context rule:
they launch the installed Firefox (preferred) or Chrome, or attach to a
Chrome that is already open, using that profile's fingerprint and this
machine's normal route. `actions` launches Firefox on the Actions machine
with the account storage_state and does not copy a Firefox profile. See
`dusk_publisher.browser_identity`.

Every step helper below raises `StepFailedError(platform, step, selector)`
on failure (never a bare Playwright TimeoutError), per the project's error
message requirement: when a selector goes stale, the error should say
exactly which step and which selector broke.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dusk_publisher.accounts.proxy import (
    PreparedProxy,
    prepare_account_proxy,
    read_proxy_sidecar,
)
from dusk_publisher.browser_identity import (
    SystemProxy,
    chrome_source_user_data,
    firefox_source_profile,
    profile_directory,
    read_system_proxy,
    redact_proxy,
    resolve_daily_family,
    sync_daily_profile,
    sync_firefox_profile,
)
from dusk_publisher.errors import StepFailedError
from dusk_publisher.firefox_fingerprint import (
    FirefoxFingerprint,
    firefox_launch_kwargs,
    firefox_user_prefs,
    load_firefox_fingerprint,
)
from dusk_publisher.platforms.human import human_pause, human_type
from dusk_publisher.logging_setup import get_logger
from dusk_publisher.settings import Settings

log = get_logger(__name__)

# Playwright's defaults that would make the window differ from a normal Chrome:
# no extensions, sRGB instead of the display profile, and the automation flag.
_IDENTITY_IGNORED_ARGS = [
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--force-color-profile=srgb",
    "--enable-automation",
    "--disable-sync",
    "--password-store=basic",
    "--use-mock-keychain",
]


@dataclass
class BrowserSession:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    attached: bool = False
    export_cookies: bool = True
    profile_dir: Path | None = None
    tunnel: PreparedProxy | None = None

    async def close(self) -> None:
        """Close what this session owns.

        A CDP attach is the user's already-open Chrome. Closing the browser
        object would quit that window, so only the tab we opened is closed
        and the DevTools connection is dropped.
        """
        try:
            if self.attached:
                try:
                    await self.page.close()
                except Exception:
                    pass
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                return
            try:
                await self.browser.close()
            finally:
                await self.playwright.stop()
        finally:
            if self.tunnel is not None:
                self.tunnel.close()
                self.tunnel = None

    async def save_storage_state(self, path: Path) -> str:
        if not self.export_cookies:
            label = str(self.profile_dir) if self.profile_dir else "daily-browser-profile"
            log.info("daily browser keeps the login in %s; not exporting the whole cookie jar", label)
            return label
        await save_storage_state(self.context, path)
        return str(path)


async def new_session(
    *,
    storage_state: Path | None,
    headless: bool,
    proxy: str | None = None,
    channel: str = "chrome",
    settings: Settings | None = None,
    identity: str | None = None,
) -> BrowserSession:
    """Open a page. Caller must call `session.close()`.

    `identity="ephemeral"` (the health probe, CI, and any caller that does
    not pass settings) keeps the old fresh-context behavior. Adapters pass
    `settings` so a desktop run uses the daily Firefox or Chrome instead.
    """
    mode = identity or (settings.browser_mode() if settings is not None else "ephemeral")
    if mode == "cdp":
        if settings is None or not settings.browser_cdp_url:
            raise RuntimeError("BROWSER_IDENTITY=cdp requires BROWSER_CDP_URL, for example http://127.0.0.1:9222")
        if proxy:
            log.info("cdp attach ignores platform proxy %s and uses that Chrome's own network", proxy)
        return await _attach_cdp(settings.browser_cdp_url)
    if mode == "remote-firefox":
        if settings is None or not settings.firefox_remote_url:
            raise RuntimeError("remote Firefox mode requires FIREFOX_REMOTE_URL")
        if proxy:
            log.info("remote Firefox ignores platform proxy %s and uses that Firefox's own network", proxy)
        return await _attach_firefox(settings.firefox_remote_url)
    if mode == "daily":
        if proxy:
            log.info("daily browser ignores platform proxy %s so the exit IP matches this machine", proxy)
        if _daily_family(settings) == "firefox":
            return await _launch_daily_firefox(settings)
        return await _launch_daily(settings, channel=channel)
    if mode == "actions":
        if proxy:
            log.info(
                "actions firefox ignores platform env proxy %s",
                redact_proxy(proxy),
            )
        return await _launch_actions_firefox(settings, storage_state)
    return await _launch_ephemeral(
        storage_state=storage_state, headless=headless, proxy=proxy, channel=channel
    )


async def _launch_ephemeral(
    *,
    storage_state: Path | None,
    headless: bool,
    proxy: str | None,
    channel: str,
) -> BrowserSession:
    from patchright.async_api import async_playwright  # lazy import, see module docstring

    playwright = await async_playwright().start()
    launch_kwargs: dict[str, Any] = {"headless": headless, "channel": channel}
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    browser = await playwright.chromium.launch(**launch_kwargs)
    context_kwargs: dict[str, Any] = {}
    if storage_state and Path(storage_state).exists():
        context_kwargs["storage_state"] = str(storage_state)
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    page.set_default_timeout(30000)
    return BrowserSession(playwright, browser, context, page)


def _daily_family(settings: Settings | None) -> str:
    if settings is not None:
        return settings.daily_family()
    firefox = firefox_source_profile()
    chrome = chrome_source_user_data()
    return resolve_daily_family(
        None,
        firefox_profile_exists=bool(firefox and firefox.is_dir()),
        chrome_profile_exists=bool(chrome and chrome.is_dir()),
    )


def xvfb_geometry(fingerprint: FirefoxFingerprint | None) -> str:
    """Framebuffer size for Xvfb, ``WIDTHxHEIGHT``.

    Both screen dimensions must be present. When ``device_pixel_ratio`` is
    greater than 0, the framebuffer is that CSS size times the ratio, rounded
    to whole pixels, so ``window.screen`` CSS pixels match the captured
    values. A missing dimension uses 1920x1080.
    """
    if fingerprint is None or fingerprint.screen_width is None or fingerprint.screen_height is None:
        return "1920x1080"
    width = fingerprint.screen_width
    height = fingerprint.screen_height
    ratio = fingerprint.device_pixel_ratio
    if ratio is not None and ratio > 0:
        width = round(width * ratio)
        height = round(height * ratio)
    return f"{width}x{height}"


def actions_firefox_launch_plan(
    fingerprint: FirefoxFingerprint | None,
    *,
    storage_state: Path | None,
    executable: str | None = None,
    base_env: Mapping[str, str] | None = None,
    account_proxy: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Firefox ``launch`` kwargs and ``new_context`` kwargs for actions mode.

    No Firefox profile directory is required. Login comes from ``storage_state``.
    ``account_proxy`` is the Playwright proxy for this account. Platform env
    proxies are not included.
    """
    launch: dict[str, Any] = {
        "channel": "moz-firefox",
        "headless": False,
    }
    executable_path = (executable or "").strip()
    if executable_path:
        launch["executable_path"] = executable_path
    extra = firefox_launch_kwargs(
        fingerprint,
        platform_proxy=None,
        account_proxy=account_proxy,
        base_env=base_env,
    )
    if extra.get("firefox_user_prefs"):
        launch["firefox_user_prefs"] = extra["firefox_user_prefs"]
    if extra.get("env") is not None:
        launch["env"] = extra["env"]
    if extra.get("proxy"):
        launch["proxy"] = extra["proxy"]
    context: dict[str, Any] = {"no_viewport": True}
    if extra.get("timezone_id"):
        context["timezone_id"] = extra["timezone_id"]
    if extra.get("extra_http_headers"):
        context["extra_http_headers"] = extra["extra_http_headers"]
    if storage_state is not None and Path(storage_state).is_file():
        context["storage_state"] = str(storage_state)
    return launch, context


def _display_number(display: str) -> str:
    text = display.strip() or ":99"
    if ":" not in text:
        raise ValueError(f"DISPLAY must look like :99, got {display!r}")
    number = text.rsplit(":", 1)[1].split(".", 1)[0]
    if not number.isdigit():
        raise ValueError(f"DISPLAY must look like :99, got {display!r}")
    return number


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _xvfb_is_up(display: str) -> bool:
    number = _display_number(display)
    if (Path("/tmp/.X11-unix") / f"X{number}").exists():
        return True
    lock_path = Path(f"/tmp/.X{number}-lock")
    if not lock_path.is_file():
        return False
    try:
        pid_text = lock_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if pid_text.isdigit() and _pid_alive(int(pid_text)):
        return True
    try:
        lock_path.unlink()
    except OSError:
        return False
    return False


def ensure_xvfb(display: str, geometry: str) -> None:
    """Start Xvfb on ``display`` when it is not already listening.

    The install script does not start a screen. Geometry is known only after
    the fingerprint file has been loaded.
    """
    os.environ["DISPLAY"] = display
    if _xvfb_is_up(display):
        log.info("Xvfb already up on %s", display)
        return
    binary = shutil.which("Xvfb")
    if not binary:
        raise RuntimeError(
            "Xvfb is not on PATH. Install it with scripts/install-actions-browser.sh before launching Firefox."
        )
    number = _display_number(display)
    log_path = Path(tempfile.gettempdir()) / f"xvfb-{number}.log"
    command = [binary, display, "-screen", "0", f"{geometry}x24", "-ac", "-nolisten", "tcp"]
    log.info("starting Xvfb %s geometry=%s", display, geometry)
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _xvfb_is_up(display):
            log.info("Xvfb ready on %s geometry=%s", display, geometry)
            return
        if proc.poll() is not None:
            detail = ""
            try:
                detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            message = f"Xvfb exited with status {proc.returncode} for {display} ({geometry})"
            if detail:
                message = f"{message}. {detail}"
            raise RuntimeError(message)
        time.sleep(0.1)
    raise RuntimeError(f"Xvfb did not open {display} within 15s ({geometry})")


async def _launch_actions_firefox(
    settings: Settings | None,
    storage_state: Path | None,
) -> BrowserSession:
    fingerprint = load_firefox_fingerprint(None if settings is None else settings.data_dir)
    geometry = xvfb_geometry(fingerprint)
    display = os.environ.get("DISPLAY", "").strip() or ":99"
    ensure_xvfb(display, geometry)
    if fingerprint is None:
        log.info("actions firefox display=%s xvfb=%s; no fingerprint file", display, geometry)
    else:
        log.info(
            "actions firefox display=%s xvfb=%s fingerprint=%s prefs=%s",
            display,
            geometry,
            fingerprint.source,
            ",".join(sorted(firefox_user_prefs(fingerprint))) or "(none)",
        )
    prepared: PreparedProxy | None = None
    account_proxy = read_proxy_sidecar(storage_state)
    if account_proxy is None:
        log.info("actions firefox uses this machine's network")
    else:
        prepared = prepare_account_proxy(account_proxy)
    launch_kwargs, context_kwargs = actions_firefox_launch_plan(
        fingerprint,
        storage_state=storage_state,
        executable=os.environ.get("FIREFOX_EXECUTABLE"),
        account_proxy=None if prepared is None else prepared.playwright,
    )
    from patchright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = None
    try:
        browser = await playwright.firefox.launch(**launch_kwargs)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.set_default_timeout(30000)
    except Exception:
        if browser is not None:
            await browser.close()
        await playwright.stop()
        if prepared is not None:
            prepared.close()
        raise
    return BrowserSession(playwright, browser, context, page, tunnel=prepared)


async def _launch_daily_firefox(settings: Settings | None) -> BrowserSession:
    source = firefox_source_profile()
    if source is None or not source.is_dir():
        raise RuntimeError(
            "Daily browser mode could not find a Firefox profile. Install Firefox "
            "or set FIREFOX_PROFILE_PATH to the directory about:support calls Profile Directory."
        )
    if (source / "parent.lock").exists():
        log.warning(
            "Firefox is open, so the cookie copy may be incomplete. Quit Firefox once if a site asks you to sign in."
        )
    dest = _daily_profile_root(settings, "daily-firefox")
    copied = sync_firefox_profile(source, dest)
    log.info(
        "daily firefox profile copy: source=%s dest=%s copied=%s",
        source,
        dest,
        ",".join(copied) or "(none, using the previous copy)",
    )
    # Platform proxies are not applied. Egress is the host default route.
    fingerprint = load_firefox_fingerprint(None if settings is None else settings.data_dir)
    if fingerprint is None:
        log.info("daily firefox network route: host default route; no fingerprint file")
    else:
        missing = fingerprint.fields_to_fill()
        log.info(
            "daily firefox network route: host default route; fingerprint=%s prefs=%s timezone=%s",
            fingerprint.source,
            ",".join(sorted(firefox_user_prefs(fingerprint))) or "(none)",
            fingerprint.timezone or "(unset)",
        )
        if fingerprint.screen_width and fingerprint.screen_height:
            log.info(
                "daily firefox screen %sx%s follows the host display; Firefox has no screen pref",
                fingerprint.screen_width,
                fingerprint.screen_height,
            )
        if missing:
            log.info("firefox fingerprint still blank: %s", ",".join(missing))

    from patchright.async_api import async_playwright

    playwright = await async_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "channel": "moz-firefox",
        "headless": False,
        "no_viewport": True,
    }
    launch_kwargs.update(firefox_launch_kwargs(fingerprint, platform_proxy=None, account_proxy=None))
    executable = os.environ.get("FIREFOX_EXECUTABLE")
    if executable:
        launch_kwargs["executable_path"] = executable
    try:
        context = await playwright.firefox.launch_persistent_context(str(dest), **launch_kwargs)
    except Exception:
        await playwright.stop()
        raise
    return await _finish_daily_session(playwright, context, dest)


async def _launch_daily(settings: Settings | None, *, channel: str) -> BrowserSession:
    source = chrome_source_user_data()
    if source is None or not source.is_dir():
        raise RuntimeError(
            "Daily browser mode could not find a Chrome profile. Install Google Chrome "
            "or set CHROME_USER_DATA_DIR to the directory chrome://version calls Profile Path's parent."
        )
    explicit_profile = settings.chrome_profile_directory if settings is not None else None
    profile = profile_directory(source, explicit_profile)
    dest_root = _daily_profile_root(settings)
    copied = sync_daily_profile(source, dest_root, profile)
    log.info(
        "daily browser profile copy: source=%s profile=%s dest=%s copied=%s",
        source,
        profile,
        dest_root,
        ",".join(copied) or "(none, using the previous copy)",
    )
    system_proxy = read_system_proxy()
    log.info("daily browser network route: %s", system_proxy.label)

    from patchright.async_api import async_playwright

    playwright = await async_playwright().start()
    args = [
        f"--profile-directory={profile}",
        "--disable-session-crashed-bubble",
    ]
    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "channel": channel,
        "no_viewport": True,
        "ignore_default_args": _IDENTITY_IGNORED_ARGS,
        "args": args,
    }
    _apply_system_proxy(launch_kwargs, args, system_proxy)
    try:
        context = await playwright.chromium.launch_persistent_context(str(dest_root), **launch_kwargs)
    except Exception:
        await playwright.stop()
        raise
    return await _finish_daily_session(playwright, context, dest_root)


async def _finish_daily_session(playwright, context, dest: Path) -> BrowserSession:
    page = context.pages[0] if context.pages else await context.new_page()
    page.set_default_timeout(30000)
    browser = context.browser
    if browser is None:
        await context.close()
        await playwright.stop()
        raise RuntimeError("the daily browser started without a browser handle")
    session = BrowserSession(
        playwright,
        browser,
        context,
        page,
        export_cookies=False,
        profile_dir=dest,
    )
    await _log_egress(context)
    return session


async def _attach_firefox(endpoint: str) -> BrowserSession:
    """Drive an already-running Firefox. Do not quit it when the task ends.

    `endpoint` is the Playwright Firefox websocket (`BrowserType.connect`),
    reachable from GitHub Actions through a private tunnel. The page's
    network is that Firefox, so the exit IP is the one it already uses.
    """
    from patchright.async_api import async_playwright

    playwright = await async_playwright().start()
    try:
        browser = await playwright.firefox.connect(endpoint)
    except Exception as exc:
        await playwright.stop()
        raise RuntimeError(
            f"Could not attach to Firefox at {endpoint}. The publisher is running "
            "in GitHub Actions and cannot see 127.0.0.1 on your PC. Publish a "
            "Playwright Firefox websocket through a private tunnel and set FIREFOX_REMOTE_URL."
        ) from exc
    context = browser.contexts[0] if browser.contexts else await browser.new_context(no_viewport=True)
    page = await context.new_page()
    page.set_default_timeout(30000)
    session = BrowserSession(
        playwright,
        browser,
        context,
        page,
        attached=True,
        export_cookies=False,
    )
    await _log_egress(context)
    return session


async def _attach_cdp(endpoint: str) -> BrowserSession:
    from patchright.async_api import async_playwright

    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.connect_over_cdp(endpoint)
    except Exception as exc:
        await playwright.stop()
        raise RuntimeError(
            f"Could not attach to {endpoint}. Start the installed Chrome with a non-default "
            "--user-data-dir and --remote-debugging-port, then set BROWSER_CDP_URL to that port."
        ) from exc
    context = browser.contexts[0] if browser.contexts else await browser.new_context(no_viewport=True)
    page = await context.new_page()
    page.set_default_timeout(30000)
    session = BrowserSession(
        playwright,
        browser,
        context,
        page,
        attached=True,
        export_cookies=False,
    )
    await _log_egress(context)
    return session


def _daily_profile_root(settings: Settings | None, name: str = "daily-chrome") -> Path:
    if settings is not None:
        root = settings.data_dir / name
    else:
        root = Path(name)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _apply_system_proxy(launch_kwargs: dict[str, Any], args: list[str], proxy: SystemProxy) -> None:
    if proxy.server:
        payload: dict[str, str] = {"server": proxy.server}
        if proxy.bypass:
            payload["bypass"] = proxy.bypass
        launch_kwargs["proxy"] = payload
        return
    if proxy.pac_url:
        args.append(f"--proxy-pac-url={proxy.pac_url}")


async def _log_egress(context) -> None:
    """Record the public IP and user agent of this exact browser, then close the probe tab."""
    page = await context.new_page()
    try:
        await page.goto("https://www.cloudflare.com/cdn-cgi/trace", wait_until="domcontentloaded", timeout=20000)
        body = await page.locator("body").inner_text()
        ip = ""
        for line in body.splitlines():
            if line.startswith("ip="):
                ip = line.split("=", 1)[1].strip()
                break
        ua = await page.evaluate("() => navigator.userAgent")
        log.info("browser egress ip=%s ua=%s", ip or "(missing)", ua)
    except Exception as exc:
        log.warning("browser egress check failed: %s", exc)
    finally:
        await page.close()


async def close_session(playwright, browser) -> None:
    try:
        await browser.close()
    finally:
        await playwright.stop()


async def save_storage_state(context, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(path))


# --- step helpers: every one raises StepFailedError with (platform, step, selector) ---


async def click_step(page, platform: str, step: str, selector: str, *, timeout: int = 15000) -> None:
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await human_pause(page)
        await loc.click()
    except Exception as exc:  # noqa: BLE001 - intentionally broad, re-raised with context
        raise StepFailedError(platform, step, selector, exc) from exc


async def click_if_present(page, selector: str, *, timeout: int = 4000) -> bool:
    """Non-fatal variant for optional UI (e.g. dismiss a dropdown, close a dialog).

    Returns False instead of raising if the element never appears — the
    caller decides whether that's actually a problem worth surfacing.
    """
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await human_pause(page)
        await loc.click()
        return True
    except Exception:
        return False


async def fill_step(page, platform: str, step: str, selector: str, text: str, *, timeout: int = 15000) -> None:
    """Click into a visible field and type one character at a time.

    Does not call ``locator.fill``. File inputs use ``set_files_step``.
    """
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await human_pause(page)
        await loc.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await human_type(page, text)
    except Exception as exc:  # noqa: BLE001
        raise StepFailedError(platform, step, selector, exc) from exc


async def set_files_step(
    page, platform: str, step: str, selector: str, paths: list[str], *, timeout: int = 60000, state: str = "attached"
) -> None:
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state=state, timeout=timeout)
        await loc.set_input_files(paths)
    except Exception as exc:  # noqa: BLE001
        raise StepFailedError(platform, step, selector, exc) from exc


async def wait_visible_step(page, platform: str, step: str, selector: str, *, timeout: int = 30000) -> None:
    try:
        await page.locator(selector).first.wait_for(state="visible", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise StepFailedError(platform, step, selector, exc) from exc


async def is_logged_out_url(page, login_url_markers: list[str]) -> bool:
    url = page.url
    return any(marker in url for marker in login_url_markers)
