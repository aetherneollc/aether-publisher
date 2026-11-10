"""Account proxy sidecar, Zero Trust decision, and handshake. No live proxy."""

from __future__ import annotations

import socket
import threading

from dusk_publisher.accounts.proxy import (
    AccountProxy,
    needs_zero_trust_tunnel,
    prepare_account_proxy,
    public_detail,
    run_proxy_check,
    socks5_handshake,
)


def test_tunnel_decision_ignores_public_endpoints():
    http_proxy = AccountProxy("http", "proxy.example", 8080, "user", "proxy-password-value")
    https_edge = AccountProxy("https", "socks.aetherneo.com", 443, "user", "proxy-password-value")
    ip_proxy = AccountProxy("socks5", "203.0.113.10", 1080, "user", "proxy-password-value")
    open_name = AccountProxy("socks5", "proxy.example", 1080, "user", "proxy-password-value")
    closed_name = AccountProxy("socks5", "socks.internal.example", 1080, "user", "proxy-password-value")
    access_name = AccountProxy("socks5", "socks.aetherneo.com", 443, "aether", "proxy-password-value")

    def answers_on_443(host, port):
        return port == 443

    def only_socks_port(host, port):
        return port == 1080

    def edge_and_stored_port(host, port):
        return port in {443, 1080}

    assert needs_zero_trust_tunnel(http_proxy, tcp_open=lambda host, port: False) is False
    assert needs_zero_trust_tunnel(https_edge, tcp_open=answers_on_443) is False
    assert needs_zero_trust_tunnel(ip_proxy, tcp_open=lambda host, port: False) is False
    assert needs_zero_trust_tunnel(open_name, tcp_open=only_socks_port) is False
    assert needs_zero_trust_tunnel(open_name, tcp_open=edge_and_stored_port) is True
    assert needs_zero_trust_tunnel(closed_name, tcp_open=lambda host, port: False) is True
    assert needs_zero_trust_tunnel(access_name, tcp_open=answers_on_443) is True
    assert needs_zero_trust_tunnel(closed_name, tcp_open=answers_on_443) is True


def test_public_endpoints_skip_cloudflared(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("public proxy must not start cloudflared")

    monkeypatch.setattr("dusk_publisher.accounts.proxy.subprocess.Popen", refuse)
    http_proxy = prepare_account_proxy(AccountProxy("http", "proxy.example", 8080, "user", "proxy-password-value"))
    ip_proxy = prepare_account_proxy(AccountProxy("socks5", "203.0.113.10", 1080, "user", "proxy-password-value"))
    named = prepare_account_proxy(
        AccountProxy("socks5", "proxy.example", 1080, "user", "proxy-password-value"),
        tcp_open=lambda host, port: port == 1080,
    )
    try:
        assert http_proxy.process is None
        assert http_proxy.playwright["server"] == "http://proxy.example:8080"
        assert ip_proxy.playwright["server"] == "socks5://203.0.113.10:1080"
        assert named.process is None
        assert "proxy-password-value" not in http_proxy.playwright["server"]
        assert "proxy-password-value" not in http_proxy.label
        assert http_proxy.label == "http://***@proxy.example:8080"
    finally:
        http_proxy.close()
        ip_proxy.close()
        named.close()


def test_access_hostname_on_443_still_forwards_with_cloudflared(monkeypatch):
    secret = "access-token-secret"
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "access-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", secret)
    monkeypatch.setattr("dusk_publisher.accounts.proxy.shutil.which", lambda name: "cloudflared")
    monkeypatch.setattr("dusk_publisher.accounts.proxy._free_local_port", lambda: 41017)
    monkeypatch.setattr("dusk_publisher.accounts.proxy._wait_for_local_port", lambda *args, **kwargs: None)
    monkeypatch.setattr("dusk_publisher.accounts.proxy.tcp_port_open", lambda host, port, timeout=3.0: port == 443)
    seen: dict = {}

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        seen["command"] = command
        return FakeProc()

    monkeypatch.setattr("dusk_publisher.accounts.proxy.subprocess.Popen", fake_popen)
    prepared = prepare_account_proxy(
        AccountProxy("socks5", "aether-socks.aetherneo.com", 443, "aether", secret)
    )
    try:
        command = seen["command"]
        assert command[command.index("--hostname") + 1] == "aether-socks.aetherneo.com"
        assert command[command.index("--url") + 1] == "127.0.0.1:41017"
        assert prepared.playwright["server"] == "socks5://127.0.0.1:41017"
        assert prepared.playwright["username"] == "aether"
        assert secret not in prepared.playwright["server"]
        assert prepared.process is not None
    finally:
        prepared.close()


def test_access_hostname_on_1080_still_forwards_with_cloudflared(monkeypatch):
    secret = "access-token-secret"
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "access-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", secret)
    monkeypatch.setattr("dusk_publisher.accounts.proxy.shutil.which", lambda name: "cloudflared")
    monkeypatch.setattr("dusk_publisher.accounts.proxy._free_local_port", lambda: 41017)
    monkeypatch.setattr("dusk_publisher.accounts.proxy._wait_for_local_port", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "dusk_publisher.accounts.proxy.tcp_port_open",
        lambda host, port, timeout=3.0: port in {443, 1080},
    )
    seen: dict = {}

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        seen["command"] = command
        return FakeProc()

    monkeypatch.setattr("dusk_publisher.accounts.proxy.subprocess.Popen", fake_popen)
    prepared = prepare_account_proxy(
        AccountProxy("socks5", "aether-socks.aetherneo.com", 1080, "aether", secret)
    )
    try:
        command = seen["command"]
        assert command[command.index("--hostname") + 1] == "aether-socks.aetherneo.com"
        assert command[command.index("--url") + 1] == "127.0.0.1:41017"
        assert prepared.playwright["server"] == "socks5://127.0.0.1:41017"
        assert prepared.playwright["username"] == "aether"
        assert secret not in prepared.playwright["server"]
        assert prepared.process is not None
    finally:
        prepared.close()


def test_private_hostname_forwards_with_cloudflared(monkeypatch):
    secret = "access-token-secret"
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "access-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", secret)
    monkeypatch.setattr("dusk_publisher.accounts.proxy.shutil.which", lambda name: "cloudflared")
    monkeypatch.setattr("dusk_publisher.accounts.proxy._free_local_port", lambda: 41017)
    monkeypatch.setattr("dusk_publisher.accounts.proxy._wait_for_local_port", lambda *args, **kwargs: None)
    monkeypatch.setattr("dusk_publisher.accounts.proxy.tcp_port_open", lambda *args, **kwargs: False)
    seen: dict = {}

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr("dusk_publisher.accounts.proxy.subprocess.Popen", fake_popen)
    prepared = prepare_account_proxy(
        AccountProxy("socks5", "socks.internal.example", 1080, "user", secret)
    )
    try:
        command = seen["command"]
        assert command[1:3] == ["access", "tcp"]
        assert command[command.index("--hostname") + 1] == "socks.internal.example"
        assert command[command.index("--url") + 1] == "127.0.0.1:41017"
        joined = " ".join(command)
        assert secret not in joined
        assert "user:" not in joined
        assert seen["env"]["TUNNEL_SERVICE_TOKEN_ID"] == "access-client-id"
        assert seen["env"]["TUNNEL_SERVICE_TOKEN_SECRET"] == secret
        assert prepared.playwright["server"] == "socks5://127.0.0.1:41017"
        assert prepared.playwright["username"] == "user"
        assert secret not in prepared.playwright["server"]
        assert prepared.label == "socks5://***@127.0.0.1:41017"
    finally:
        prepared.close()


def test_missing_cloudflared_is_error_not_a_cookie_result(monkeypatch):
    monkeypatch.setattr("dusk_publisher.accounts.proxy.shutil.which", lambda name: None)
    monkeypatch.setattr("dusk_publisher.accounts.proxy.tcp_port_open", lambda *args, **kwargs: False)
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "access-client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "access-token-secret")
    outcome, detail = run_proxy_check(
        AccountProxy("socks5", "socks.internal.example", 1080, "user", "access-token-secret")
    )
    assert outcome == "error"
    assert "cookie-invalid" not in outcome
    assert "access-token-secret" not in detail
    assert "cloudflared" in detail


def test_no_proxy_is_error():
    assert run_proxy_check(None) == ("error", "account has no proxy")


def test_public_detail_strips_userinfo():
    secret = "proxy-password-value"
    cleaned = public_detail(f"failed socks5://user:{secret}@203.0.113.10:1080", (secret,))
    assert secret not in cleaned
    assert "socks5://***@" in cleaned


def _read(conn: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        piece = conn.recv(remaining)
        if not piece:
            break
        chunks.append(piece)
        remaining -= len(piece)
    return b"".join(chunks)


def test_socks_handshake_success_and_failure():
    secret = "proxy-password-value"
    ready = threading.Event()
    box: dict[str, int] = {}

    def serve() -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        box["port"] = server.getsockname()[1]
        ready.set()
        conn, _addr = server.accept()
        with conn:
            _read(conn, 3)
            conn.sendall(b"\x05\x02")
            header = _read(conn, 2)
            _read(conn, header[1])
            password_len = _read(conn, 1)[0]
            _read(conn, password_len)
            conn.sendall(b"\x01\x00")
            _read(conn, 4)
            host_len = _read(conn, 1)[0]
            _read(conn, host_len + 2)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)
    assert socks5_handshake("127.0.0.1", box["port"], "user", secret) is True
    thread.join(2)

    refused = threading.Event()

    def reject() -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        box["bad"] = server.getsockname()[1]
        refused.set()
        conn, _addr = server.accept()
        with conn:
            _read(conn, 3)
            conn.sendall(b"\x05\xff")
        server.close()

    bad = threading.Thread(target=reject, daemon=True)
    bad.start()
    assert refused.wait(2)
    outcome, detail = run_proxy_check(AccountProxy("socks5", "127.0.0.1", box["bad"], "user", secret))
    assert outcome == "proxy-down"
    assert secret not in detail
    bad.join(2)
