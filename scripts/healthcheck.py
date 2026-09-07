#!/usr/bin/env python3
"""Docker `HEALTHCHECK` entrypoint. stdlib only (no deps beyond Python
itself needed inside the healthcheck process, since it runs isolated per
Docker's health-check machinery).

Exit code 0 = healthy, 1 = unhealthy, matching Docker's convention.

Special case: if nothing is listening on $HEALTHZ_PORT at all (connection
refused), we exit 0 (healthy) rather than 1. That's intentional: this
healthcheck also runs for one-off CLI invocations (`docker compose run
dusk-publisher queue run ...`, `... youtube login ...`) which never start
the /healthz server in the first place — those aren't "the daemon is up
but broken", they're "this was never meant to be a long-running healthz-
serving process", and shouldn't be flagged unhealthy for it. Only an actual
non-200 response (the daemon IS up, and IS reporting itself unhealthy)
fails the check.
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.error
import urllib.request

PORT = int(os.environ.get("HEALTHZ_PORT", "8080"))
URL = f"http://127.0.0.1:{PORT}/healthz"
TIMEOUT_S = 5


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                print(f"healthz OK: {body[:300]}")
                return 0
            print(f"healthz reported unhealthy (HTTP {resp.status}): {body[:300]}", file=sys.stderr)
            return 1
    except urllib.error.HTTPError as exc:
        # e.g. 503 from our own handler when HealthState.ok is False.
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        print(f"healthz reported unhealthy (HTTP {exc.code}): {body[:300]}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, ConnectionRefusedError, socket.timeout, OSError) as exc:
        print(f"no /healthz server listening on :{PORT} ({exc}) — treating as healthy (not `daemon run`)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
