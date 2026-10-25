from __future__ import annotations

import importlib.util
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vault-smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "vault-smoke.yml"

ACCESS_HTML = (
    b"<html>\r\n<head><title>302 Found</title></head>\r\n"
    b"<body>\r\n<center><h1>302 Found</h1></center>\r\n"
    b"<hr><center>cloudflare</center>\r\n</body>\r\n</html>\r\n"
)


def load_smoke():
    spec = importlib.util.spec_from_file_location("vault_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load_smoke()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.last_path = self.path
        self.server.last_headers = {key.lower(): value for key, value in self.headers.items()}
        mode = self.server.mode
        if mode == "ok":
            body = b'{"accounts":[{"id":"a1","name":"secret-account-name","status":"ready"}]}'
            status = 200
            content_type = "application/json"
        elif mode == "redirect":
            body = ACCESS_HTML
            status = 302
            content_type = "text/html; charset=UTF-8"
        elif mode == "html200":
            body = b"<html><body>not json</body></html>"
            status = 200
            content_type = "text/html"
        elif mode == "unauthorized":
            body = b'{"error":"unauthorized"}'
            status = 401
            content_type = "application/json"
        elif mode == "echo":
            body = (
                f"id={self.headers.get('CF-Access-Client-Id', '')} "
                f"secret={self.headers.get('CF-Access-Client-Secret', '')} "
                f"auth={self.headers.get('Authorization', '')}"
            ).encode()
            status = 200
            content_type = "text/plain"
        else:
            body = b'{"accounts":{}}'
            status = 200
            content_type = "application/json"
        self.send_response(status)
        if mode == "redirect":
            self.send_header("Location", "https://example.cloudflareaccess.com/cdn-cgi/access/login/vault")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.mode = "ok"
    httpd.last_path = None
    httpd.last_headers = {}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()


def _env(base_url: str, **overrides: str) -> dict[str, str]:
    env = {
        "AETHER_VAULT_URL": base_url,
        "AETHER_VAULT_RUNNER_TOKEN": "runner-token-value",
        "CF_ACCESS_CLIENT_ID": "access-client-id-value",
        "CF_ACCESS_CLIENT_SECRET": "access-client-secret-value",
    }
    env.update(overrides)
    return env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_workflow_invokes_diagnosing_script_without_jq_pipe():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python3 scripts/vault-smoke.py" in text
    for name in smoke.SECRET_ENV:
        assert name in text
    assert "| jq" not in text


def test_passes_when_accounts_array_is_json(server):
    server.mode = "ok"
    port = server.server_address[1]
    result = _run(_env(f"http://127.0.0.1:{port}"))
    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout
    assert "secret-account-name" not in result.stdout + result.stderr
    assert server.last_path == "/api/runner/accounts"
    assert server.last_headers["authorization"] == "Bearer runner-token-value"
    assert server.last_headers["cf-access-client-id"] == "access-client-id-value"
    assert server.last_headers["cf-access-client-secret"] == "access-client-secret-value"


def test_access_redirect_is_not_json_and_prints_status_type_and_body(server):
    server.mode = "redirect"
    port = server.server_address[1]
    result = _run(_env(f"http://127.0.0.1:{port}/"))
    assert result.returncode == 1
    assert "response is not JSON" in result.stderr
    assert "HTTP status: 302" in result.stderr
    assert "Content-Type: text/html; charset=UTF-8" in result.stderr
    assert "<html>" in result.stderr
    assert "302 Found" in result.stderr
    assert "Service Auth" in result.stderr
    assert "runner-token-value" not in result.stderr
    assert "access-client-secret-value" not in result.stderr


def test_http_200_html_fails_as_not_json(server):
    server.mode = "html200"
    port = server.server_address[1]
    result = _run(_env(f"http://127.0.0.1:{port}"))
    assert result.returncode == 1
    assert "response is not JSON" in result.stderr
    assert "HTTP status: 200" in result.stderr
    assert "Content-Type: text/html" in result.stderr
    assert "not json" in result.stderr


def test_unauthorized_json_names_runner_token_secret(server):
    server.mode = "unauthorized"
    port = server.server_address[1]
    result = _run(_env(f"http://127.0.0.1:{port}"))
    assert result.returncode == 1
    assert "HTTP status: 401" in result.stderr
    assert "Content-Type: application/json" in result.stderr
    assert "unauthorized" in result.stderr
    assert "RUNNER_API_TOKEN" in result.stderr


def test_body_preview_redacts_secrets(server):
    server.mode = "echo"
    port = server.server_address[1]
    result = _run(_env(f"http://127.0.0.1:{port}"))
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "runner-token-value" not in combined
    assert "access-client-id-value" not in combined
    assert "access-client-secret-value" not in combined
    assert "[redacted]" in result.stderr
    assert "HTTP status: 200" in result.stderr


def test_url_with_path_fails_before_request(server):
    server.mode = "ok"
    port = server.server_address[1]
    result = _run(_env(f"http://127.0.0.1:{port}/api"))
    assert result.returncode == 1
    assert "no path" in result.stderr
    assert "/api" in result.stderr
    assert server.last_path is None


def test_empty_secret_is_named_and_not_printed():
    env = _env("https://vault.example")
    env["AETHER_VAULT_RUNNER_TOKEN"] = ""
    result = _run(env)
    assert result.returncode == 1
    assert "AETHER_VAULT_RUNNER_TOKEN" in result.stderr
    assert "access-client-secret-value" not in result.stderr


def test_failure_report_limits_preview_to_200_bytes():
    body = b"x" * 500
    report = smoke.failure_report(200, "text/plain", body, [])
    assert report is not None
    assert "response is not JSON" in report
    marker = "Body (first 200 bytes): "
    preview = report.split(marker, 1)[1].splitlines()[0]
    assert preview == repr("x" * 200)
