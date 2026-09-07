"""Minimal structured-ish logging setup (stdlib only, no extra dependency).

Every log line is prefixed with the platform/account when relevant so a
long-running daemon's logs are greppable, e.g.:

    2026-09-07 12:00:01 INFO [youtube:zh_main] uploading demo.mp4
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. re-entrant CLI calls in tests)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
