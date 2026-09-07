"""Extremely small `GET /healthz` HTTP server, stdlib only (`http.server`)
— deliberately not FastAPI/aiohttp/uvicorn, per "极轻量" in the brief: this
is a monitoring side-channel, not a real API, and shouldn't need its own
dependency tree or event loop integration. Runs in a background daemon
thread so it keeps answering even if the asyncio event loop driving the
publish/poll pipeline is busy (a slow browser automation step should not
make the container look unhealthy).

Returns:
  - 200 + JSON body when `HealthState.ok` is True
  - 503 + JSON body when it's False (this is what flips `docker ps` to
    "unhealthy" via the Dockerfile's `HEALTHCHECK`)
  - 404 for any other path

No auth: this endpoint intentionally exposes no secrets (no cookie
contents, no STUDIO_AGENT_KEY), only operational metadata. See README for
the recommendation to not publish this port to the public internet anyway.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from dusk_publisher.health import HealthState
from dusk_publisher.logging_setup import get_logger

log = get_logger(__name__)


def _make_handler(get_state: Callable[[], HealthState]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
            log.debug("healthz: " + fmt, *args)

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            if self.path.rstrip("/") != "/healthz":
                self.send_response(404)
                self.end_headers()
                return
            state = get_state()
            body = json.dumps(state.to_debug_dict(), default=str).encode("utf-8")
            self.send_response(200 if state.ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def start_healthz_server(port: int, get_state: Callable[[], HealthState]) -> Callable[[], None]:
    """Start the server in a background thread; returns a `stop()` callable."""
    server = ThreadingHTTPServer(("0.0.0.0", port), _make_handler(get_state))
    thread = threading.Thread(target=server.serve_forever, name="dusk-publisher-healthz", daemon=True)
    thread.start()
    log.info("healthz server listening on :%d/healthz", port)

    def stop() -> None:
        server.shutdown()
        server.server_close()

    return stop
