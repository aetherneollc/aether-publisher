# dusk-publisher — multi-platform video publisher
#
# Base image ships Playwright's Chromium + all OS-level dependencies already
# installed, which patchright (a drop-in Playwright replacement) reuses.
ARG PLAYWRIGHT_VERSION=1.47.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HEADLESS=1

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY README.md THIRD_PARTY_NOTICES.md LICENSE ./

RUN pip install --no-cache-dir -e . \
    # patchright vendors its own Chromium build metadata but reuses the
    # Playwright browser cache; this makes sure the "chrome" channel used by
    # every adapter is actually present.
    && python -m patchright install chrome --with-deps || python -m patchright install chrome

RUN mkdir -p /data/accounts /data/cache

VOLUME ["/data"]

EXPOSE 8080

# `daemon run` starts an in-process GET /healthz on $HEALTHZ_PORT (default
# 8080) — see healthz_server.py. It answers 200 when healthy, 503 when not
# (browser can't launch / disk too full / cookie volume unwritable / control
# plane unreachable — see health.py), so `docker ps` shows healthy/unhealthy
# without needing to guess from logs. `queue run` / one-off CLI commands
# don't start this server, so the healthcheck is a no-op success for those
# (curl against a port nothing is listening on would otherwise report
# unhealthy for a command that was never meant to be long-running; we treat
# "can't connect" as healthy here on purpose — start-period below covers the
# brief startup window for `daemon run` itself).
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD ["python3", "/app/scripts/healthcheck.py"]

ENTRYPOINT ["dusk-publisher"]
CMD ["daemon", "run"]
