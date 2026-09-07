"""The one publishing engine, shared by local-queue mode and control-plane
pull mode — it only depends on the `TaskSource` abstraction, never on which
concrete source is behind it.

`run_once()` does a single pass over currently-available work. `queue run`
(local mode) calls it repeatedly until a pass makes no further progress
(handles multi-hop dependency chains within one file); the daemon (pull
mode) calls it once per poll interval, since gating across polls is exactly
what the control plane's own `/ops/work` filtering is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dusk_publisher.accounts.router import AccountRouter
from dusk_publisher.errors import AccountNotFoundError, AssetNotFoundError, ControlPlaneError, NeedsLoginError
from dusk_publisher.logging_setup import get_logger
from dusk_publisher.models import Publication, PubStatus
from dusk_publisher.pipeline.dropback import find_parent, inject_into_description, parent_is_ready
from dusk_publisher.platforms.base import PublishRequest
from dusk_publisher.platforms.registry import get_adapter
from dusk_publisher.settings import Settings
from dusk_publisher.sources.base import TaskSource

log = get_logger(__name__)


@dataclass
class RunSummary:
    published: list[tuple[str, str | None]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    needs_login: list[tuple[str, str, str]] = field(default_factory=list)  # (pub_id, platform, account)
    skipped_waiting_on_parent: list[str] = field(default_factory=list)
    # Snapshot of how many approved rows fetch_work() returned at the start
    # of this pass — used by the daemon to report `queue_depth` in health
    # telemetry (see pipeline/daemon.py, health.py).
    pending_count: int = 0

    @property
    def made_progress(self) -> bool:
        return bool(self.published or self.failed)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"published={len(self.published)} failed={len(self.failed)} "
            f"needs_login={len(self.needs_login)} waiting_on_parent={len(self.skipped_waiting_on_parent)}"
        )


class PublisherPipeline:
    def __init__(self, source: TaskSource, router: AccountRouter, settings: Settings):
        self.source = source
        self.router = router
        self.settings = settings

    async def run_once(self) -> RunSummary:
        pending, langs = await self.source.fetch_work()
        summary = RunSummary(pending_count=len(pending))

        # Local-queue sources can see every record (any status), which is
        # what lets us self-enforce parent-published gating and resolve the
        # parent's URL for dropback ourselves. Control-plane sources cannot
        # look up arbitrary rows by id, so they rely on the denormalized
        # `Publication.parent_url` field the control plane now populates
        # directly on the short's own row (contract v1 fix, adopted after we
        # flagged that /ops/work's approved-only filter made the parent's
        # URL otherwise unreachable — see README "Shared contract").
        supports_lookup = hasattr(self.source, "all_publications")

        for pub in pending:
            parent_url: str | None = None
            if pub.is_gated_on_parent():
                if supports_lookup:
                    all_pubs: list[Publication] = await self.source.all_publications()
                    parent = find_parent(all_pubs, pub.parent_pub_id)  # type: ignore[arg-type]
                    if not parent_is_ready(parent):
                        log.info("[%s] waiting on parent %s to be published", pub.pub_id, pub.parent_pub_id)
                        summary.skipped_waiting_on_parent.append(pub.pub_id)
                        continue
                    parent_url = parent.url
                elif pub.parent_url:
                    parent_url = pub.parent_url
                else:
                    # Defensive fallback: the control plane didn't send parent_url on
                    # this row even though it's gated on a parent. Contract says gating
                    # itself already happened server-side, so we still publish — just
                    # without an injected dropback link — rather than block forever.
                    log.warning(
                        "[%s] short is gated on parent %s but no parent_url was provided "
                        "by the control plane — publishing without an injected dropback link",
                        pub.pub_id,
                        pub.parent_pub_id,
                    )

            await self._publish_one(pub, langs, parent_url, summary)

        return summary

    async def _publish_one(self, pub: Publication, langs: dict, parent_url: str | None, summary: RunSummary) -> None:
        try:
            account = self.router.resolve(pub.platform.value, pub.lang)
        except AccountNotFoundError as exc:
            log.error("[%s] %s", pub.pub_id, exc)
            await self.source.report(pub.pub_id, "failed", error=str(exc))
            summary.failed.append((pub.pub_id, str(exc)))
            return

        try:
            claimed = await self.source.claim(pub)
        except (AssetNotFoundError, ControlPlaneError) as exc:
            log.error("[%s] %s", pub.pub_id, exc)
            await self.source.report(pub.pub_id, "failed", error=str(exc))
            summary.failed.append((pub.pub_id, str(exc)))
            return

        description = pub.description
        if parent_url:
            description = inject_into_description(description, parent_url)

        req = PublishRequest(
            video_path=claimed.video_path,
            cover_path=claimed.cover_path,
            title=pub.title,
            description=description,
            tags=pub.tags,
            lang=pub.lang,
            visibility=pub.visibility,
            playlist=pub.playlist,
            scheduled_at=pub.scheduled_at,
            parent_url=parent_url,
            language_meta=langs.get(pub.lang),
        )

        adapter = get_adapter(pub.platform.value, self.settings)
        log.info("[%s] publishing via %s:%s", pub.pub_id, pub.platform.value, account)
        try:
            result = await adapter.publish(account, req)
        except NeedsLoginError as exc:
            log.warning("[%s] %s", pub.pub_id, exc)
            # `needs_login` (not `failed`) per contract v1 update: the control
            # plane rolls the row back to `approved` for retry once a human
            # re-logs in, and flags the account's cookie_ok=False, instead of
            # treating this as a hard/terminal failure. LocalQueueSource
            # mirrors that same rollback itself (see sources/local.py).
            await self.source.report(pub.pub_id, "needs_login", error=str(exc))
            summary.needs_login.append((pub.pub_id, pub.platform.value, account))
            return
        except Exception as exc:  # noqa: BLE001 - never let one bad task kill the daemon loop
            log.exception("[%s] publish raised an unexpected error", pub.pub_id)
            await self.source.report(pub.pub_id, "failed", error=str(exc))
            summary.failed.append((pub.pub_id, str(exc)))
            return

        if result.ok:
            log.info("[%s] published: %s", pub.pub_id, result.url)
            await self.source.report(pub.pub_id, "published", url=result.url)
            summary.published.append((pub.pub_id, result.url))
        else:
            log.error("[%s] publish failed: %s", pub.pub_id, result.error)
            await self.source.report(pub.pub_id, "failed", error=result.error)
            summary.failed.append((pub.pub_id, result.error or "unknown error"))

    async def run_until_stable(self, max_passes: int = 10) -> RunSummary:
        """Repeat `run_once()` until a pass makes no further progress.

        This is what lets local-queue mode resolve a same-file dependency
        chain (long publishes in pass 1 -> its short becomes unblocked and
        publishes in pass 2) in a single `queue run` invocation.
        """
        total = RunSummary()
        for i in range(max_passes):
            summary = await self.run_once()
            total.published.extend(summary.published)
            total.failed.extend(summary.failed)
            total.needs_login.extend(summary.needs_login)
            total.skipped_waiting_on_parent = summary.skipped_waiting_on_parent
            if not summary.made_progress:
                log.info("no progress in pass %d, stopping (%s)", i + 1, summary)
                break
        return total
