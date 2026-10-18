"""dusk-publisher CLI.

    dusk-publisher youtube login   --account NAME
    dusk-publisher youtube check   --account NAME
    dusk-publisher youtube publish --account NAME --file demo.mp4 --title "..." [...]
    dusk-publisher tiktok  ...
    dusk-publisher x       ...
    dusk-publisher accounts list
    dusk-publisher queue run   [--file queue.yaml]
    dusk-publisher daemon run
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer

from dusk_publisher.accounts.router import AccountRouter
from dusk_publisher.logging_setup import get_logger, setup_logging
from dusk_publisher.models import Visibility
from dusk_publisher.pipeline.runner import PublisherPipeline
from dusk_publisher.platforms.base import PublishRequest
from dusk_publisher.platforms.registry import get_adapter, list_platforms
from dusk_publisher.settings import get_settings
from dusk_publisher.sources.local import LocalQueueSource

setup_logging()
log = get_logger("dusk_publisher.cli")

app = typer.Typer(add_completion=False, help="Multi-platform video publisher (YouTube/TikTok/X).")

platform_app = typer.Typer(help="Per-platform login/check/publish.")
queue_app = typer.Typer(help="Standalone local queue mode (no control plane needed).")
daemon_app = typer.Typer(help="Pull-mode daemon against the 暮色工房 control plane.")
batch_app = typer.Typer(help="Bounded pull-mode batch for ephemeral CI runners.")
accounts_app = typer.Typer(help="Inspect configured accounts.")


def _run(coro):
    return asyncio.run(coro)


def _platform_command_group(platform_name: str) -> typer.Typer:
    sub = typer.Typer(help=f"{platform_name} login/check/publish.")

    @sub.command("login")
    def login(account: str = typer.Option(..., "--account", help="Account name (cookie file key).")):
        """Interactive login: opens a real browser window, waits for you to
        sign in, then saves the session cookie to
        /data/accounts/<platform>/<account>.json."""
        settings = get_settings()
        adapter = get_adapter(platform_name, settings)
        result = _run(adapter.login(account))
        if result.ok:
            typer.echo(f"[{platform_name}:{account}] login OK -> {result.account_file}")
        else:
            typer.echo(f"[{platform_name}:{account}] login FAILED: {result.detail}")
            raise typer.Exit(code=1)

    @sub.command("check")
    def check(account: str = typer.Option(..., "--account", help="Account name (cookie file key).")):
        """Headless probe: is the saved cookie still accepted by the platform?"""
        settings = get_settings()
        adapter = get_adapter(platform_name, settings)
        result = _run(adapter.check(account))
        typer.echo(f"[{platform_name}:{account}] {'OK' if result.ok else 'INVALID'}: {result.detail}")
        if not result.ok:
            raise typer.Exit(code=1)

    @sub.command("publish")
    def publish(
        account: str = typer.Option(..., "--account"),
        file: Path = typer.Option(..., "--file", exists=True, help="Video file to upload."),
        title: str = typer.Option("", "--title"),
        description: str = typer.Option("", "--description"),
        tags: str = typer.Option("", "--tags", help="Comma-separated tags/hashtags."),
        cover: Optional[Path] = typer.Option(None, "--cover", help="Thumbnail/cover image."),
        lang: str = typer.Option("", "--lang", help="Language code, for logging/routing metadata only."),
        playlist: Optional[str] = typer.Option(None, "--playlist", help="YouTube playlist name (series)."),
        visibility: Visibility = typer.Option(Visibility.public, "--visibility"),
        scheduled_at: Optional[str] = typer.Option(None, "--scheduled-at", help="ISO8601. TikTok only, currently."),
        parent_url: Optional[str] = typer.Option(
            None, "--parent-url", help="Long-video URL to inject for dropback (shorts only)."
        ),
    ):
        """Publish one video directly (bypasses both queue sources — useful
        for testing a single account/adapter in isolation)."""
        settings = get_settings()
        adapter = get_adapter(platform_name, settings)
        req = PublishRequest(
            video_path=file,
            cover_path=cover,
            title=title,
            description=description,
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            lang=lang,
            visibility=visibility,
            playlist=playlist,
            scheduled_at=scheduled_at,
            parent_url=parent_url,
        )
        result = _run(adapter.publish(account, req))
        if result.ok:
            typer.echo(f"[{platform_name}:{account}] published -> {result.url}")
        else:
            typer.echo(f"[{platform_name}:{account}] publish FAILED: {result.error}")
            raise typer.Exit(code=1)

    return sub


for _platform in list_platforms():
    app.add_typer(_platform_command_group(_platform), name=_platform)


@accounts_app.command("list")
def accounts_list():
    """Show every configured account, its platform/lang binding, and cookie status."""
    settings = get_settings()
    router = AccountRouter.from_file(settings.accounts_file)
    if not router.all():
        typer.echo(f"No accounts configured in {settings.accounts_file}")
        return
    for entry in router.all():
        cookie = get_adapter(entry.platform, settings).cookie_file(entry.name)
        typer.echo(
            f"{entry.platform:8s} {entry.name:20s} lang={entry.lang or '(generic)':6s} "
            f"cookie={'present' if cookie.exists() else 'MISSING'} ({cookie})"
        )


@accounts_app.command("capture")
def accounts_capture(
    platform: Optional[str] = typer.Option(None, "--platform", help="Only capture one platform."),
    account: Optional[str] = typer.Option(None, "--account", help="Only capture one account name."),
):
    """One guided login pass over configured accounts.

    Each account opens a visible browser. Sign in and complete any 2FA; the
    resulting Playwright storage state is written under DATA_DIR/accounts.
    Run this on a trusted desktop/VNC host, never in a headless CI job.
    """
    settings = get_settings()
    router = AccountRouter.from_file(settings.accounts_file)
    selected = [
        entry for entry in router.all()
        if (not platform or entry.platform == platform.lower())
        and (not account or entry.name == account)
    ]
    if not selected:
        typer.echo("No configured accounts matched the requested filters.")
        raise typer.Exit(code=1)

    failures = 0
    for index, entry in enumerate(selected, start=1):
        typer.echo(f"[{index}/{len(selected)}] Open login for {entry.platform}:{entry.name}")
        result = _run(get_adapter(entry.platform, settings).login(entry.name))
        if result.ok:
            typer.echo(f"  saved -> {result.account_file}")
        else:
            failures += 1
            typer.echo(f"  FAILED: {result.detail}")
    if failures:
        raise typer.Exit(code=1)


@accounts_app.command("check-all")
def accounts_check_all(
    platform: Optional[str] = typer.Option(None, "--platform", help="Only check one platform."),
    account: Optional[str] = typer.Option(None, "--account", help="Only check one account name."),
):
    """Validate every configured cookie with a real headless browser."""
    settings = get_settings()
    router = AccountRouter.from_file(settings.accounts_file)
    selected = [
        entry for entry in router.all()
        if (not platform or entry.platform == platform.lower())
        and (not account or entry.name == account)
    ]
    if not selected:
        typer.echo("No configured accounts matched the requested filters.")
        raise typer.Exit(code=1)
    failures = 0
    for entry in selected:
        result = _run(get_adapter(entry.platform, settings).check(entry.name))
        typer.echo(f"[{entry.platform}:{entry.name}] {'OK' if result.ok else 'INVALID'}: {result.detail}")
        failures += 0 if result.ok else 1
    if failures:
        raise typer.Exit(code=1)


app.add_typer(accounts_app, name="accounts")


@queue_app.command("run")
def queue_run(
    file: Path = typer.Option(None, "--file", help="Local queue YAML/JSON file (defaults to $LOCAL_QUEUE_FILE)."),
    media_root: Optional[Path] = typer.Option(None, "--media-root", help="Base dir for asset_key/cover_key."),
):
    """Standalone mode: read a local YAML/JSON task queue, publish everything
    that's ready, respecting parent_pub_id gating for dropback shorts. Safe
    to re-run — already-published rows are skipped."""
    settings = get_settings()
    queue_file = file or settings.local_queue_file
    source = LocalQueueSource(queue_file, media_root=media_root)
    router = AccountRouter.from_file(settings.accounts_file)
    pipeline = PublisherPipeline(source, router, settings)
    summary = _run(pipeline.run_until_stable())

    typer.echo(f"published: {len(summary.published)}")
    for pub_id, url in summary.published:
        typer.echo(f"  ok    {pub_id} -> {url}")
    for pub_id, plat, acct in summary.needs_login:
        typer.echo(f"  LOGIN {pub_id} needs `dusk-publisher {plat} login --account {acct}`")
    for pub_id, err in summary.failed:
        typer.echo(f"  FAIL  {pub_id}: {err}")
    for pub_id in summary.skipped_waiting_on_parent:
        typer.echo(f"  WAIT  {pub_id} (parent not published yet)")

    if summary.failed:
        raise typer.Exit(code=1)


app.add_typer(queue_app, name="queue")


@daemon_app.command("run")
def daemon_run():
    """Long-running loop: poll /ops/work, claim, download, publish, report,
    and periodically heartbeat cookie health. This is the container's
    default CMD."""
    settings = get_settings()
    if not settings.studio_agent_key:
        typer.echo("WARNING: STUDIO_AGENT_KEY is empty — /ops/* requests will get 401 unauthorized.")
    from dusk_publisher.pipeline.daemon import run_forever

    _run(run_forever(settings))


app.add_typer(daemon_app, name="daemon")


@batch_app.command("run")
def batch_run(
    max_tasks: int = typer.Option(3, "--max-tasks", min=1, max=50),
    max_minutes: int = typer.Option(240, "--max-minutes", min=1, max=330),
    platforms: str = typer.Option("all", "--platforms", help="Comma-separated or 'all'."),
):
    """Process a bounded task batch, then exit.

    Designed for ephemeral runners: tasks are grouped by platform/account,
    executed sequentially, and left for a later run once the time budget is
    reached.
    """
    settings = get_settings()
    router = AccountRouter.from_file(settings.accounts_file)
    from dusk_publisher.sources.control_plane import ControlPlaneSource

    source = ControlPlaneSource(
        base_url=settings.control_plane_url,
        api_key=settings.studio_agent_key,
        agent_id=settings.agent_id,
        cache_dir=settings.cache_dir,
        cf_access_client_id=settings.cf_access_client_id,
        cf_access_client_secret=settings.cf_access_client_secret,
    )
    selected_platforms = [] if platforms.strip().lower() == "all" else platforms.split(",")
    pipeline = PublisherPipeline(source, router, settings)

    async def execute():
        try:
            if settings.account_vault_url and settings.account_vault_runner_token:
                from dusk_publisher.accounts.vault import AccountVaultClient

                vault = AccountVaultClient(
                    settings.account_vault_url,
                    settings.account_vault_runner_token,
                    cf_access_client_id=settings.cf_access_client_id,
                    cf_access_client_secret=settings.cf_access_client_secret,
                )
                try:
                    synced = await vault.sync_ready_accounts(settings.accounts_dir)
                    typer.echo(f"vault_synced={synced}")
                finally:
                    await vault.aclose()
            return await pipeline.run_once(
                max_tasks=max_tasks,
                max_seconds=max_minutes * 60,
                platforms=selected_platforms,
                group_by_account=True,
            )
        finally:
            await source.aclose()

    summary = _run(execute())
    typer.echo(
        f"selected={summary.selected_count} published={len(summary.published)} "
        f"failed={len(summary.failed)} needs_login={len(summary.needs_login)} "
        f"budget_stop={summary.stopped_by_budget}"
    )
    for pub_id, url in summary.published:
        typer.echo(f"  ok    {pub_id} -> {url}")
    for pub_id, err in summary.failed:
        typer.echo(f"  FAIL  {pub_id}: {err}")
    for pub_id, plat, acct in summary.needs_login:
        typer.echo(f"  LOGIN {pub_id}: {plat}:{acct}")
    if summary.failed or summary.needs_login:
        raise typer.Exit(code=1)


app.add_typer(batch_app, name="batch")


if __name__ == "__main__":
    app()
