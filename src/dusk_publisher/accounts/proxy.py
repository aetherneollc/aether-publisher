"""Per-account proxy sidecar, Zero Trust tunnel, and reachability probe.

Actions Firefox uses this account proxy. ``YT_PROXY``, ``X_PROXY``, and
``TIKTOK_PROXY`` are not read here.

A SOCKS hostname that is not an IP address is reached with
``cloudflared access tcp``, then Firefox uses
``socks5://<user>@127.0.0.1:<local port>``. A public DNS name that answers on
443 is the Cloudflare Access / HTTPS edge, not a SOCKS server, so it still
uses that tunnel even when the stored proxy port also completes a TCP
handshake. A public HTTP proxy, an IP literal, or a SOCKS hostname whose
proxy port accepts TCP and whose port 443 does not, is passed to Firefox
directly. Logs never include the username or password.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from dusk_publisher.logging_setup import get_logger

log = get_logger(__name__)

_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+(?::[^\s/@]+)?@")


class ProxySetupError(RuntimeError):
    """The tunnel cannot be started, so the proxy was not tested."""


class ProxyUnreachable(RuntimeError):
    """The proxy or its tunnel did not accept a connection."""


@dataclass(frozen=True)
class AccountProxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
        }


@dataclass
class PreparedProxy:
    """Playwright proxy config plus an optional ``cloudflared`` process."""

    playwright: dict[str, str]
    label: str
    process: subprocess.Popen[Any] | None = None
    log_path: Path | None = None

    def close(self) -> None:
        _stop_process(self.process)
        self.process = None
        if self.log_path is not None:
            try:
                self.log_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.log_path = None


def proxy_sidecar_path(cookie_path: Path) -> Path:
    path = Path(cookie_path)
    return path.with_name(f"{path.stem}.proxy.json")


def parse_account_proxy(payload: Any) -> AccountProxy | None:
    """``None`` and JSON null mean the account has no proxy."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("proxy must be an object or null")
    scheme = str(payload.get("scheme") or "").strip().lower()
    if scheme in {"socks", "socks5", "socks5h"}:
        scheme = "socks5"
    elif scheme not in {"http", "https"}:
        raise ValueError("unsupported proxy scheme")
    host = str(payload.get("host") or "").strip()
    if not host:
        raise ValueError("proxy host is required")
    port = _port(payload.get("port"))
    username = _optional_text(payload.get("username"))
    password = _optional_text(payload.get("password"))
    return AccountProxy(scheme, host, port, username, password)


def write_proxy_sidecar(cookie_path: Path, proxy: AccountProxy | None) -> None:
    """Write ``<cookie>.proxy.json`` mode 0600, or remove it when proxy is null."""
    sidecar = proxy_sidecar_path(cookie_path)
    if proxy is None:
        if sidecar.exists():
            sidecar.unlink()
            log.info("account proxy cleared %s", Path(cookie_path).name)
        return
    write_private_text(sidecar, json.dumps(proxy.as_json(), ensure_ascii=True) + "\n")
    log.info("account proxy %s %s", Path(cookie_path).name, redact_account_proxy(proxy))


def read_proxy_sidecar(cookie_path: Path | None) -> AccountProxy | None:
    if cookie_path is None:
        return None
    sidecar = proxy_sidecar_path(Path(cookie_path))
    if not sidecar.is_file():
        return None
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    return parse_account_proxy(payload)


def redact_account_proxy(proxy: AccountProxy, *, host: str | None = None, port: int | None = None) -> str:
    shown_host = host or proxy.host
    shown_port = proxy.port if port is None else port
    if proxy.username or proxy.password:
        return f"{proxy.scheme}://***@{shown_host}:{shown_port}"
    return f"{proxy.scheme}://{shown_host}:{shown_port}"


def public_detail(text: str, secrets: tuple[str, ...] = ()) -> str:
    """One log/result line with proxy passwords and URL userinfo removed."""
    cleaned = " ".join(text.split())
    for secret in secrets:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "***")
    cleaned = _URL_USERINFO.sub(r"\1***@", cleaned)
    return cleaned[:400]


def needs_zero_trust_tunnel(
    proxy: AccountProxy,
    *,
    tcp_open: Callable[[str, int], bool] | None = None,
) -> bool:
    """True when Firefox must reach this SOCKS host through ``cloudflared``.

    HTTP proxies and IP literals are public endpoints. A SOCKS hostname is a
    direct server only when its own proxy port accepts TCP and port 443 does
    not. Port 443 is the Cloudflare Access / HTTPS edge: a public DNS name
    that answers there is not a SOCKS listener. Cloudflare's edge also
    completes a TCP handshake on other ports, so that handshake alone must
    not skip the tunnel.
    """
    if proxy.scheme in {"http", "https"}:
        return False
    if _is_ip(proxy.host):
        return False
    opener = tcp_open or tcp_port_open
    try:
        if opener(proxy.host, 443):
            return True
        return not opener(proxy.host, proxy.port)
    except OSError:
        return True


def playwright_proxy(proxy: AccountProxy, *, host: str | None = None, port: int | None = None) -> dict[str, str]:
    """Playwright proxy dict. The password stays out of ``server``."""
    shown_host = host or proxy.host
    shown_port = proxy.port if port is None else port
    config = {"server": f"{proxy.scheme}://{_host_for_url(shown_host)}:{shown_port}"}
    if proxy.username:
        config["username"] = proxy.username
    if proxy.password:
        config["password"] = proxy.password
    return config


def prepare_account_proxy(
    proxy: AccountProxy,
    *,
    tcp_open: Callable[[str, int], bool] | None = None,
) -> PreparedProxy:
    """Return the Firefox proxy. Starts ``cloudflared access tcp`` when required."""
    if needs_zero_trust_tunnel(proxy, tcp_open=tcp_open):
        process, local_port, log_path = start_zero_trust_tunnel(proxy.host)
        prepared = PreparedProxy(
            playwright=playwright_proxy(proxy, host="127.0.0.1", port=local_port),
            label=redact_account_proxy(proxy, host="127.0.0.1", port=local_port),
            process=process,
            log_path=log_path,
        )
        log.info("actions firefox proxy %s via cloudflared", prepared.label)
        return prepared
    prepared = PreparedProxy(
        playwright=playwright_proxy(proxy),
        label=redact_account_proxy(proxy),
    )
    log.info("actions firefox proxy %s", prepared.label)
    return prepared


def start_zero_trust_tunnel(hostname: str) -> tuple[subprocess.Popen[Any], int, Path]:
    """Run ``cloudflared access tcp --hostname <host> --url 127.0.0.1:<port>``.

    The Access service token is taken from ``CF_ACCESS_CLIENT_ID`` and
    ``CF_ACCESS_CLIENT_SECRET`` and passed only as the child environment
    ``cloudflared`` reads (``TUNNEL_SERVICE_TOKEN_ID`` / ``TUNNEL_SERVICE_TOKEN_SECRET``).
    It is not placed on the command line.
    """
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ProxySetupError("CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET are required for a private proxy")
    binary = shutil.which("cloudflared")
    if not binary:
        raise ProxySetupError("cloudflared is not on PATH")
    local_port = _free_local_port()
    log_path = Path(tempfile.gettempdir()) / f"cloudflared-access-{local_port}.log"
    env = os.environ.copy()
    env["TUNNEL_SERVICE_TOKEN_ID"] = client_id
    env["TUNNEL_SERVICE_TOKEN_SECRET"] = client_secret
    command = [
        binary,
        "access",
        "tcp",
        "--hostname",
        hostname,
        "--url",
        f"127.0.0.1:{local_port}",
    ]
    log.info("starting cloudflared access tcp hostname=%s url=127.0.0.1:%s", hostname, local_port)
    handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT)
    except OSError as exc:
        handle.close()
        log_path.unlink(missing_ok=True)
        raise ProxySetupError("could not start cloudflared") from exc
    handle.close()
    try:
        _wait_for_local_port(
            local_port,
            process,
            log_path,
            secrets=(client_id, client_secret),
        )
    except Exception:
        _stop_process(process)
        log_path.unlink(missing_ok=True)
        raise
    return process, local_port, log_path


def probe_prepared_proxy(prepared: PreparedProxy, *, timeout: float = 8.0) -> bool:
    """SOCKS5 handshake plus CONNECT, or HTTP CONNECT. False when the proxy is down."""
    parsed = urlparse(prepared.playwright["server"])
    host = parsed.hostname or ""
    port = parsed.port or 0
    username = prepared.playwright.get("username", "")
    password = prepared.playwright.get("password", "")
    if parsed.scheme in {"socks5", "socks"}:
        return socks5_handshake(host, port, username, password, timeout=timeout)
    if parsed.scheme in {"http", "https"}:
        return http_connect(host, port, username, password, timeout=timeout)
    raise ProxySetupError("unsupported proxy scheme")


def run_proxy_check(proxy: AccountProxy | None) -> tuple[str, str]:
    """Return ``proxy-up``, ``proxy-down``, or ``error``. Never a cookie outcome."""
    if proxy is None:
        return "error", "account has no proxy"
    secrets = (proxy.password, proxy.username)
    prepared: PreparedProxy | None = None
    try:
        prepared = prepare_account_proxy(proxy)
        if probe_prepared_proxy(prepared):
            return "proxy-up", "proxy accepted a handshake"
        return "proxy-down", "proxy handshake failed"
    except ProxySetupError as exc:
        return "error", public_detail(str(exc), secrets)
    except (ProxyUnreachable, OSError) as exc:
        return "proxy-down", public_detail(str(exc), secrets)
    finally:
        if prepared is not None:
            prepared.close()


def tcp_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def socks5_handshake(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    timeout: float = 8.0,
    dest_host: str = "www.cloudflare.com",
    dest_port: int = 443,
) -> bool:
    """Return True when a SOCKS5 greeting, auth, and CONNECT succeed."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            method = 2 if username or password else 0
            sock.sendall(bytes((5, 1, method)))
            version, chosen = _recv_exact(sock, 2)
            if version != 5:
                return False
            if chosen == 2:
                user = username.encode("utf-8")
                secret = password.encode("utf-8")
                if len(user) > 255 or len(secret) > 255:
                    return False
                sock.sendall(bytes((1, len(user))) + user + bytes((len(secret),)) + secret)
                auth_version, status = _recv_exact(sock, 2)
                if auth_version != 1 or status != 0:
                    return False
            elif chosen != 0:
                return False
            dest = dest_host.encode("idna")
            if len(dest) > 255:
                return False
            sock.sendall(bytes((5, 1, 0, 3, len(dest))) + dest + dest_port.to_bytes(2, "big"))
            reply = _recv_exact(sock, 4)
            if reply[1] != 0:
                return False
            atyp = reply[3]
            if atyp == 1:
                _recv_exact(sock, 6)
            elif atyp == 4:
                _recv_exact(sock, 18)
            elif atyp == 3:
                length = _recv_exact(sock, 1)[0]
                _recv_exact(sock, length + 2)
            return True
    except OSError:
        return False


def http_connect(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    timeout: float = 8.0,
    dest_host: str = "www.cloudflare.com",
    dest_port: int = 443,
) -> bool:
    """Return True when the HTTP proxy answers CONNECT with status 200."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            lines = [
                f"CONNECT {dest_host}:{dest_port} HTTP/1.1",
                f"Host: {dest_host}:{dest_port}",
            ]
            if username or password:
                import base64

                token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
                lines.append(f"Proxy-Authorization: Basic {token}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
            data = b""
            while b"\r\n" not in data and len(data) < 256:
                piece = sock.recv(256 - len(data))
                if not piece:
                    break
                data += piece
            status = data.split(b"\r\n", 1)[0]
            return status.startswith(b"HTTP/1.1 200") or status.startswith(b"HTTP/1.0 200")
    except OSError:
        return False


def write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _wait_for_local_port(
    port: int,
    process: subprocess.Popen[Any],
    log_path: Path,
    *,
    secrets: tuple[str, ...],
) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = _redacted_log(log_path, secrets)
            message = f"cloudflared exited with status {process.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise ProxyUnreachable(message)
        if tcp_port_open("127.0.0.1", port, timeout=0.2):
            return
        time.sleep(0.1)
    _stop_process(process)
    raise ProxyUnreachable("cloudflared did not open the local forward")


def _redacted_log(path: Path, secrets: tuple[str, ...]) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return public_detail(text, secrets)


def _stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _free_local_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        piece = sock.recv(remaining)
        if not piece:
            raise OSError("proxy closed the connection")
        chunks.append(piece)
        remaining -= len(piece)
    return b"".join(chunks)


def _is_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, host)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return True
    except OSError:
        return False


def _host_for_url(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _port(value: Any) -> int:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
        raise ValueError("proxy port must be an integer")
    if not 1 <= value <= 65535:
        raise ValueError("proxy port is out of range")
    return value


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("proxy username and password must be strings")
    return value


def secrets_of(proxy: AccountProxy | None) -> tuple[str, ...]:
    if proxy is None:
        return ()
    return (proxy.password, proxy.username)