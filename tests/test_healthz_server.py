"""Starts the real background-thread HTTP server and hits it with a real
socket connection (127.0.0.1) — no mocking of http.server — to prove the
200-vs-503 gating and JSON shape actually work end to end."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from dusk_publisher.health import HealthState
from dusk_publisher.healthz_server import start_healthz_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_healthz_returns_200_when_ok():
    port = _free_port()
    state = HealthState()
    state.browser_ok = True
    state.disk_ok, state.disk_free_mb = True, 5000
    state.cookie_volume_ok = True
    state.control_plane_ok = True
    stop = start_healthz_server(port, lambda: state)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
            assert body["ok"] is True
            assert body["disk_free_mb"] == 5000
    finally:
        stop()


def test_healthz_returns_503_when_not_ok():
    port = _free_port()
    state = HealthState()
    state.browser_ok = False
    state.browser_error = "chromium launch failed: boom"
    stop = start_healthz_server(port, lambda: state)
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)
            assert False, "expected HTTPError for 503"
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            body = json.loads(exc.read())
            assert body["ok"] is False
            assert "browser_launch_failed" in body["last_error"]
    finally:
        stop()


def test_healthz_returns_404_for_other_paths():
    port = _free_port()
    state = HealthState()
    stop = start_healthz_server(port, lambda: state)
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
            assert False, "expected HTTPError for 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        stop()
