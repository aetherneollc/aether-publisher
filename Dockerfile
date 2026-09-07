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

ENTRYPOINT ["dusk-publisher"]
CMD ["daemon", "run"]
