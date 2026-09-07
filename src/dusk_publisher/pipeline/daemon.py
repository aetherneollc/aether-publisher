"""Pull-mode daemon: poll the control plane, publish, report, heartbeat.

Loop, forever (per the brief: "常驻守护进程...掉线能自恢复"):
  - every `poll_interval_seconds`: run_once() against the control plane.
  - every `check_interval_seconds`: run `check()` for every configured
    account and send the results to `/ops/heartbeat`, so the control plane
    can surface "XX account needs re-login" to a human before a scheduled
    publish actually fails.

Any exception from a single pass (network blip, control-plane hiccup) is
caught, logged, and the loop keeps going after a short backoff — a single
bad poll should never kill the daemon.
"""

from __future__ import annotations

import asyncio
import time

from dusk_publisher.accounts.router import AccountRouter
from dusk_publisher.logging_setup import get_logger
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.platforms.registry import get_adapter
from dusk_publisher.settings import Settings
from dusk_publisher.sources.control_plane import ControlPlaneSource

log = get_logger(__name__)


async def _heartbeat_sweep(router: AccountRouter, settings: Settings, source: ControlPlaneSource) -> None:
    accounts_payload = []
    for entry in router.all():
        adapter = get_adapter(entry.platform, settings)
        result = await adapter.check(entry.name)
        accounts_payload.append({"platform": entry.platform, "name": entry.name, "cookie_ok": result.ok})
        if not result.ok:
            log.warning("[%s:%s] cookie check failed: %s", entry.platform, entry.name, result.detail)
    if accounts_payload:
        try:
            await source.heartbeat(accounts_payload)
        except Exception:
            log.exception("heartbeat POST failed (will retry next sweep)")


async def run_forever(settings: Settings) -> None:
    router = AccountRouter.from_file(settings.accounts_file)
    source = ControlPlaneSource(
        base_url=settings.control_plane_url,
        api_key=settings.studio_agent_key,
        agent_id=settings.agent_id,
        cache_dir=settings.cache_dir,
    )
    pipeline = PublisherPipeline(source, router, settings)

    last_check = 0.0
    try:
        while True:
            try:
                summary = await pipeline.run_once()
                if summary.made_progress or summary.needs_login:
                    log.info("poll complete: %s", summary)
            except Exception:
                log.exception("poll pass failed, will retry after backoff")

            now = time.monotonic()
            if now - last_check >= settings.check_interval_seconds:
                try:
                    await _heartbeat_sweep(router, settings, source)
                except Exception:
                    log.exception("heartbeat sweep failed, will retry next cycle")
                last_check = now

            await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await source.aclose()
