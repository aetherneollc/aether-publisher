# dusk-publisher

A self-hostable, Docker-ized **multi-platform video publisher** for YouTube,
TikTok, and X (Twitter). It uploads long-form videos and short-form
"trailers" that drive traffic back to them, using real browser automation
and per-account cookie files — no fragile, compliance-gated official APIs.

It works two ways, and you can use either or both:

1. **Standalone / local queue mode** — point it at a YAML/JSON file
   describing what to publish, run one command, done. No server required.
   This is a useful little tool on its own.
2. **Pull mode** — as a long-running daemon that polls a control plane's
   `/ops/*` API for work, claims/downloads assets, publishes, and reports
   back. This is how it plugs into **暮色工房 (dusk workshop)**, the anime
   production pipeline this project was built alongside — but the control
   plane API is generic enough that anything implementing the same four
   endpoints could drive it.

## Why browser automation, not official APIs

This is the single most important design decision here, so it's worth
stating plainly (this reasoning — and the "wait for 100% before publishing"
rule below — come from
[`social-auto-upload`](https://github.com/dreammis/social-auto-upload), see
[Credits](#credits--license)):

> Videos uploaded through YouTube's Data API from an **unaudited** Google
> Cloud project get force-locked to "Private" and cannot be made public
> without passing Google's compliance audit process — which is impractical
> for a personal channel or small team. Browser automation against YouTube
> **Studio** has no such restriction: it publishes exactly what a human
> clicking around Studio would get.

The same "drive the real web UI, not a locked-down API" logic applies to
TikTok and X here too, and it's why every platform uses the same
login/check/publish shape built on cookie files rather than OAuth apps.

**Corollary rule, also load-bearing:** browser-driven uploads stream while
the browser tab stays open. If you click "Publish"/"Post" (or close the
browser) before the upload progress reaches 100%, the upload gets **cut off
mid-transfer** instead of completing. Every adapter here explicitly polls
the upload/processing progress indicator and waits for completion before
clicking the final publish button — see `wait_for_upload_complete()` /
`_wait_upload_processed()` / `_wait_media_processed()` in each
`platforms/<name>/adapter.py`.

## Architecture

```
dusk-publisher/
├── src/dusk_publisher/
│   ├── models.py            Publication record (shared contract shape)
│   ├── settings.py          env-var configuration
│   ├── errors.py            NeedsLoginError / StepFailedError / ...
│   ├── accounts/
│   │   ├── cookies.py       /data/accounts/<platform>/<account>.json convention
│   │   └── router.py        (platform, lang) -> account name, with generic fallback
│   ├── sources/             "where does work come from"
│   │   ├── base.py          TaskSource ABC (fetch_work/claim/report/heartbeat)
│   │   ├── local.py         standalone: reads/writes a local YAML/JSON queue
│   │   └── control_plane.py pull-mode: talks to 暮色工房's /ops/* API
│   ├── platforms/           "how do we actually publish"
│   │   ├── base.py          PlatformAdapter ABC (login/check/publish)
│   │   ├── browser.py       shared patchright session + step helpers
│   │   ├── youtube/         Studio browser automation (long-form)
│   │   ├── tiktok/          short-form, supports scheduling
│   │   └── x/               short-form, original implementation
│   ├── pipeline/
│   │   ├── dropback.py      the growth-loop: parent_pub_id -> inject URL
│   │   ├── runner.py        the one engine, works with any TaskSource
│   │   └── daemon.py        infinite poll loop for pull mode + health/heartbeat cadences
│   ├── health.py            browser/disk/cookie-volume/control-plane probes
│   ├── healthz_server.py    tiny stdlib GET /healthz HTTP server (daemon mode only)
│   └── cli.py                `dusk-publisher ...` (Typer)
├── config/                  accounts.example.yaml, local_queue.example.yaml
├── scripts/make_fake_video.py, scripts/healthcheck.py (Docker HEALTHCHECK)
├── tests/                   see "Development" below
├── Dockerfile / docker-compose.yml   both define HEALTHCHECK
└── THIRD_PARTY_NOTICES.md
```

The same `PublisherPipeline` engine drives both modes — it only depends on
the `TaskSource` interface, never on whether that source is a local file or
an HTTP API. `LocalQueueSource` and `ControlPlaneSource` both resolve
whatever they're given down to a **local file path** before handing it to a
platform adapter, so adapters never know or care where the video came from.

## Quick start (Docker)

```bash
cp .env.example .env            # fill in STUDIO_AGENT_KEY etc. if using pull mode
cp config/accounts.example.yaml config/accounts.yaml   # edit to your accounts
mkdir -p queue && cp config/local_queue.example.yaml queue/local_queue.yaml  # if using local mode

docker compose up -d --build
docker compose logs -f
```

The container's default command is `daemon run` (pull mode). Cookies persist
in the `dusk-publisher-data` named volume, mounted at `/data`, per the
`/data/accounts/<platform>/<account>.json` convention.

### Logging in to a platform (interactive, needs a display)

`login` always opens a **real, visible** browser window — there is no
way around this, a human has to type credentials / 2FA. That means it does
not work inside a headless container as-is. Two practical options:

```bash
# Option A: run login from a machine with a display (or over X11/VNC
# forwarding into the container), then copy the resulting cookie file
# into the volume:
python -m venv .venv && . .venv/bin/activate
pip install -e .
export DATA_DIR=./local-data
dusk-publisher youtube login --account yt_zh_main
docker cp ./local-data/accounts/youtube/yt_zh_main.json \
  dusk-publisher:/data/accounts/youtube/yt_zh_main.json

# Option B: exec into the running container with a forwarded X server /
# VNC (e.g. via xvfb-run + a VNC bridge) and run the same login command
# there directly. Left to your VPS setup since it's environment-specific.
```

Once logged in, `check` and `publish` run perfectly headless and are what
the daemon uses day to day.

## Local queue mode (standalone, no control plane needed)

```bash
dusk-publisher queue run --file queue/local_queue.yaml --media-root queue/media
```

The queue file uses the exact same publication-record shape as the
control-plane contract (see below), plus a `languages:` block. `asset_key`
/ `cover_key` are resolved as paths relative to `--media-root` (or
absolute). The file doubles as the state store: `status`/`url`/`error`/
`published_at` get written back in place after every attempt, so re-running
is idempotent — already-published rows are skipped, failed ones can just be
re-run once whatever was wrong is fixed.

Dropback gating (see next section) is fully self-enforced in this mode: a
short with `parent_pub_id` set is skipped until that parent's row reaches
`status: published`, at which point its URL gets woven into the short's
description/caption/tweet automatically. Try it:

```bash
cp config/local_queue.example.yaml queue/local_queue.yaml
mkdir -p queue/media
python scripts/make_fake_video.py queue/media/story1_ep01.mp4 --cover queue/media/story1_ep01.jpg
cp queue/media/story1_ep01.mp4 queue/media/story1_ep01_short01.zh.mp4

dusk-publisher queue run --file queue/local_queue.yaml --media-root queue/media
# -> long video reaches the YouTube adapter, reports "needs login", exits 0.
# -> both shorts print "WAIT ... (parent not published yet)": gating works.
```

This is exactly the self-test in `tests/test_local_runner_e2e.py` and
`tests/test_cli_queue_smoke.py` — see [Development](#development--tests).

## Pull mode: integrating with 暮色工房

Set in `.env`:

```
STUDIO_AGENT_KEY=<shared secret>
CONTROL_PLANE_URL=https://anime-factory.hero-30b.workers.dev
AGENT_ID=dusk-publisher-01
```

`dusk-publisher daemon run` runs three independent, decoupled loops (see
[Health checks & monitoring](#health-checks--monitoring) for why they're
decoupled):
- every `POLL_INTERVAL_SECONDS` (default 60s): `GET /ops/work` → for each
  returned publication, `POST /ops/claim` (get signed asset/cover URLs,
  download them) → publish via the matching platform adapter →
  `POST /ops/report`.
- every `CHECK_INTERVAL_SECONDS` (default 1800s): the real, per-account
  `check()` (launches a browser per account) to refresh cookie validity.
- every `HEARTBEAT_INTERVAL_SECONDS` (default 60s, **fires even when
  idle**): `POST /ops/heartbeat` with every account's last-known
  `cookie_ok` plus a `health` object — see below.

### Shared contract v1 (consumed as-is, not owned by this project)

```
GET  /ops/work?agent=<id>   -> {"publications": [...], "languages": {...}}
POST /ops/claim             {"pub_id", "agent"} -> {"ok", "asset_url", "cover_url", "pub"}
POST /ops/report            {"pub_id", "status": "published"|"failed"|"needs_login", "url", "error"}
POST /ops/heartbeat         {"agent", "accounts": [{"platform","name","cookie_ok"}], "health": {...}}
```
Auth header on every request: `X-Studio-Agent-Key: <STUDIO_AGENT_KEY>`.
401 body: `{"ok": false, "error": "unauthorized"}`.

Publication record fields: `pub_id, story_id, episode_code, lang, platform
(youtube|tiktok|x), account, kind (long|short), asset_key, cover_key,
title, description, tags, status, url, parent_pub_id, parent_url,
scheduled_at, published_at, error`.

### Contract gaps — resolved this round ✅

Both gaps flagged in the previous round were fixed on the control-plane
side and adopted here:

1. **`parent_url` denormalization.** The control plane now includes the
   resolved `parent_url` directly on a short's own `/ops/work` row
   (`Publication.parent_url`). `PublisherPipeline` uses it directly for any
   `TaskSource` that can't look up other rows by id (i.e. `ControlPlaneSource`
   — detected via `hasattr(source, "all_publications")`); local queue mode
   still resolves it itself via `parent_pub_id` (see `pipeline/dropback.py`),
   since it can see every record regardless of status anyway. A defensive
   fallback remains: if a gated short somehow arrives with no `parent_url`
   set, the pipeline still publishes it (server-side gating is trusted) but
   logs a warning and skips the link injection, rather than blocking forever.
2. **`cover_url` on `/ops/claim`.** `ControlPlaneSource.claim()` now
   downloads it when present; a failed cover download is still non-fatal
   (same treatment as a missing thumbnail everywhere else in this project).
3. **`needs_login` status on `/ops/report`.** Adopted as specified:
   `PublisherPipeline` now reports `status="needs_login"` (not `"failed"`)
   when an account has no valid/working cookie. Local queue mode mirrors the
   control plane's own behavior itself (no server to do it for us): it
   rolls the row back to `status: approved` so the next `queue run` retries
   automatically once a human logs in, rather than leaving it stuck.

### Asset path correction — no code change needed

The corrected long-form path
(`stories/<story_id>/episodes/<EP>/final/<EP>.<lang>.mp4`) doesn't affect
this project: `asset_key`/`cover_key` are always treated as opaque strings
here — `LocalQueueSource` resolves them relative to `--media-root`,
`ControlPlaneSource` never inspects the path shape at all (it only ever
uses `Path(asset_key).suffix` to pick a local cache-file extension after
downloading from whatever signed URL `/ops/claim` hands back). No path
convention is hardcoded anywhere in `dusk-publisher`, so nothing needed to
change here — flagging that this was checked, not assumed.

None of the above blocks local queue mode, which never depended on the
control plane in the first place.

## Health checks & monitoring

A `daemon run` container on an unattended VPS is otherwise a black box: is
it dead, or just idle? Did Chromium quietly stop working after an `apt
upgrade`? Is the cookie volume still writable? None of that is visible from
`docker ps` alone unless something actively checks and reports it. This
project does that in three parts:

### 1. `GET /healthz` inside the container

`daemon run` starts a tiny stdlib-only HTTP server (`healthz_server.py`, no
FastAPI/uvicorn — just `http.server`) on `$HEALTHZ_PORT` (default `8080`).
It returns:
- **200** + JSON body when healthy.
- **503** + JSON body when not, with `last_error` naming exactly which
  check failed.

```bash
$ curl -s http://localhost:8080/healthz | python -m json.tool
{
  "ok": false,
  "version": "0.1.0",
  "uptime_s": 143,
  "browser_ok": false,
  "disk_free_mb": 85088,
  "queue_depth": 0,
  "last_error": "browser_launch_failed: patchright not installed: No module named 'patchright'",
  "last_success_at": null,
  "checks": { "...": "breakdown of every individual probe, for humans" }
}
```
(^ real output from this sandbox, where patchright is deliberately not
installed — see [Development](#development--tests) — which is exactly the
"browser silently broken" failure mode this probe exists to catch.)

Four failure modes are covered, checked independently, named specifically
in `last_error` (priority order when several are broken at once: browser →
disk → cookie volume → control plane):

| Check | What it actually does | Catches |
|---|---|---|
| **process alive** | implicit — if `/healthz` answers at all, the event loop is alive | a hung/crashed daemon (Docker's own curl timing out flips `docker ps` to unhealthy with no help from us) |
| **`browser_ok`** | *actually launches* a headless Chromium via patchright and closes it again — not just "is patchright importable" | broken/missing Chromium deps, a bad image rebuild, disk-full-mid-`patchright install`, wrong CPU arch — the single most likely thing to silently break unattended |
| **`disk_ok`** | `shutil.disk_usage(DATA_DIR)` vs. `DISK_MIN_FREE_MB` (default 1024) | disk filling up from downloaded videos/cache, silently failing every future download |
| **`cookie_volume_ok`** | writes + reads back + deletes a marker file in `ACCOUNTS_FILE`'s directory | `/data` volume mounted read-only, permission errors after a `docker compose down -v` recreate |
| **`control_plane_ok`** (pull mode only) | reflects the most recent `/ops/*` request's outcome | control plane unreachable/misconfigured `STUDIO_AGENT_KEY`/DNS issues |

The expensive one (`browser_ok`, spawns a real Chromium process) runs on
its own `HEALTH_CHECK_INTERVAL_SECONDS` cadence (default 300s), decoupled
from the cheap ones (disk/cookie-volume, effectively free, refreshed every
poll) and from the heartbeat POST cadence (below) — a slow browser check
should never delay a heartbeat, and vice versa.

**Design call I made, flagging it explicitly:** `control_plane_ok` factors
into the overall `ok`/`last_error`, which does mean a container can show
"unhealthy" purely because of a transient network blip to the control
plane, even though its own resources (browser/disk/cookie volume) are
completely fine. I judged this correct for `daemon run` specifically —
`/healthz` only ever runs in that mode, and a publisher daemon that can't
reach the thing it's supposed to pull work from *is* materially not doing
its job — but it's a defensible design choice either way, not a hard
requirement from the brief's checklist. If you'd rather this be a purely
self-contained container check (matching more typical container-healthcheck
conventions), it's a one-line change in `health.py::HealthState.ok`.

Local queue mode never starts this server at all (there's no daemon to
monitor — a one-shot `queue run` either succeeds or prints an error to
stdout/exits nonzero, which is already fully visible).

### 2. `docker ps` / `docker inspect` integration

Both the `Dockerfile`'s `HEALTHCHECK` and `docker-compose.yml`'s
`healthcheck:` block run `scripts/healthcheck.py` (stdlib-only, no `curl`
dependency — the base image may not have it) every 30s, which just curls
`/healthz` and exits 0/1 accordingly:

```bash
docker ps                                   # STATUS column shows "healthy"/"unhealthy"
docker inspect --format='{{json .State.Health}}' dusk-publisher | python -m json.tool
```

One-off CLI invocations (`docker compose run dusk-publisher queue run ...`,
`... youtube login ...`) never start the `/healthz` server, so
`scripts/healthcheck.py` deliberately treats "connection refused" as
healthy (exit 0) — only an actual non-200 response (the daemon *is* up and
*is* reporting itself unhealthy) fails the check. Otherwise every
one-off command would show as "unhealthy" for no reason.

`/healthz` is **not published to the host** by default (see
`docker-compose.yml`) — it has no auth, and shouldn't leak operational
metadata to the public internet on an always-on VPS. `docker ps`/`docker
inspect` work regardless, since `HEALTHCHECK` runs *inside* the container.
Uncomment the commented-out `ports:` block (bound to `127.0.0.1`, not
`0.0.0.0`) if you want to `curl` it from the VPS host directly.

### 3. Reporting health to 暮色工房 via `/ops/heartbeat`

Every `HEARTBEAT_INTERVAL_SECONDS` (default 60s), **regardless of whether
there was any work that cycle**, the daemon POSTs:

```json
{
  "agent": "vps-01",
  "accounts": [{"platform": "tiktok", "name": "...", "cookie_ok": true}],
  "health": {
    "ok": true,
    "version": "0.1.0",
    "uptime_s": 3600,
    "browser_ok": true,
    "disk_free_mb": 20480,
    "queue_depth": 2,
    "last_error": null,
    "last_success_at": "2026-09-07T14:00:00Z"
  }
}
```
`health`'s field names match the spec exactly, no extras (the extra
per-check breakdown only appears in `/healthz`'s own JSON body, not here).
This is the fixed-cadence heartbeat that lets a human watching the control
plane's UI distinguish "container alive but idle" (heartbeats keep arriving,
`ok: true`, `queue_depth: 0`) from "container dead" (heartbeats stop
arriving entirely) — previously indistinguishable, since both looked like
"nothing happened."

`accounts[].cookie_ok` comes from the last `check()` sweep
(`CHECK_INTERVAL_SECONDS` cadence, not refreshed every heartbeat — a
per-account browser launch is too expensive to do every 60s), so it can lag
reality by up to `CHECK_INTERVAL_SECONDS`; `health` itself is fresher
(`disk`/`cookie_volume`/`control_plane` every poll cycle, `browser_ok` every
`HEALTH_CHECK_INTERVAL_SECONDS`).

### What I could not actually test

Everything above is unit-tested (`tests/test_health.py`,
`tests/test_healthz_server.py`, `tests/test_control_plane.py`) and was
manually smoke-tested end to end — ran the real `daemon run` process
against an unreachable control plane, confirmed `/healthz` correctly
reported `503` with `browser_ok: false` ("patchright not installed", since
this sandbox doesn't have it) and `control_plane_ok: false` ("connection
attempts failed"), and confirmed the daemon logged the failure and kept
running rather than crashing. What I could **not** test, since it needs a
working patchright/Chromium install (this sandbox intentionally doesn't
have one — see [Development](#development--tests)) and an actual Docker
daemon (`docker.sock` access denied in this sandbox too):
- `browser_ok: true` in a real, working deployment.
- The actual `docker ps` "healthy" transition after `--start-period`
  elapses, and the "unhealthy" transition after `--retries` consecutive
  failures.
- `disk_ok: false` under a genuinely full disk (simulated with an absurdly
  high threshold in tests instead — real behavior, fake threshold).

See the deployment checklist below for exactly how to verify these
yourself once you have real Docker + a real VPS.

### First deployment self-check checklist

I cannot build or run this image in this sandbox (`docker.sock` is
permission-denied here too — same limitation you already hit). Run these
on the actual VPS after `docker compose up -d --build`, in order:

```bash
# 1. Did the build even produce an image, and how big is it?
#    (Playwright's base image + Chromium is large — a few GB is normal.
#    If this is suspiciously small, e.g. <500MB, the browser install step
#    likely failed silently.)
docker images dusk-publisher:local

# 2. Did `patchright install chrome` actually put a browser binary on disk?
#    (This is the #1 thing that silently breaks: the pip install succeeds,
#    but the browser download/extract step fails partway — e.g. disk full
#    during build, or the base image's OS deps drifted from patchright's
#    expectations.)
docker compose run --rm dusk-publisher python -c \
  "from patchright.sync_api import sync_playwright; \
   p = sync_playwright().start(); b = p.chromium.launch(headless=True); \
   print('chromium OK:', b.version); b.close(); p.stop()"
# Expect: "chromium OK: <version string>". Anything else (traceback,
# hang, "Executable doesn't exist") means the browser install is broken —
# check the build log for the `patchright install chrome --with-deps` step,
# and confirm the base image tag in Dockerfile's `PLAYWRIGHT_VERSION` ARG
# is a real published tag.

# 3. Is the daemon actually running and reporting healthy?
docker compose ps                      # STATUS column: "Up (healthy)"
docker inspect --format='{{.State.Health.Status}}' dusk-publisher
docker compose logs --tail=50 dusk-publisher

# 4. Cross-check /healthz directly (container-internal, no port publish needed):
docker compose exec dusk-publisher python3 /app/scripts/healthcheck.py; echo "exit=$?"
docker compose exec dusk-publisher python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz').read().decode())"

# 5. Is the cookie volume actually the named volume, and does it survive
#    a restart? (Catches "forgot to mount /data" / "cookies in the wrong
#    path" before you waste time re-logging-in for nothing.)
docker volume inspect dusk-publisher_dusk-publisher-data
docker compose restart dusk-publisher
docker compose exec dusk-publisher ls -la /data/accounts
```

**Common build failure causes**, roughly in order of likelihood:
1. **`patchright install chrome --with-deps` fails silently** because the
   Dockerfile's `||` fallback (`python -m patchright install chrome`
   without `--with-deps`) succeeded instead, downloading the browser binary
   but not its OS-level shared-library dependencies. Symptom: build
   "succeeds", but step 2 above fails with a shared-library error
   (`error while loading shared libraries: ...`). Fix: the base image
   (`mcr.microsoft.com/playwright/python`) should already have every OS dep
   Playwright/patchright needs — if `--with-deps` itself is failing, check
   for `apt-get`/network issues during the build, not a patchright bug.
2. **Wrong/EOL `PLAYWRIGHT_VERSION` build arg** — Microsoft only keeps a
   handful of tags published at `mcr.microsoft.com/playwright/python`;
   an old/typo'd tag makes the `FROM` line fail outright with "manifest
   not found". Check available tags if this happens.
3. **Disk full on the build host mid-build** — Chromium + its deps are
   several hundred MB; a nearly-full VPS disk can make the `RUN patchright
   install` step fail partway with a confusing error unrelated to disk
   space on its face. `df -h` before building.
4. **`HEALTHCHECK` shows "unhealthy" forever** even though logs look fine —
   almost always means `HEALTHZ_PORT` inside the container doesn't match
   what `scripts/healthcheck.py` is checking (only relevant if you changed
   the env var without also updating the `HEALTHCHECK`'s implicit default),
   or the daemon crashed on startup before ever binding the port — check
   `docker compose logs` for a traceback in the first few seconds.

## Account routing

Edit `config/accounts.yaml` (see `accounts.example.yaml`):

```yaml
accounts:
  - platform: youtube
    name: yt_zh_main
    lang: zh
  - platform: youtube
    name: yt_en_ja_shared
    lang: null   # generic fallback, used for any lang with no dedicated entry
```

Routing rule: look up by `(platform, lang)`; if nothing matches, fall back
to that platform's `lang: null` entry; if there isn't one either, fail
loudly (`AccountNotFoundError`) rather than silently posting to the wrong
account. This makes "one account per language" and "one account for
everything" both pure config choices — no code branches either way.
Languages are **never hardcoded**: they come from each `Publication.lang`
and the `languages` registry (`/ops/work`'s `languages` field, or the local
queue file's own `languages:` block).

## Platform status & how to use each one

Every platform exposes the same three commands:

```bash
dusk-publisher <platform> login   --account NAME
dusk-publisher <platform> check   --account NAME
dusk-publisher <platform> publish --account NAME --file video.mp4 --title "..." [--description ...] [--tags a,b] [--cover thumb.jpg] [--visibility public|unlisted|private] [--playlist "..."] [--scheduled-at 2026-09-10T12:00:00Z] [--parent-url https://...]
```

| Platform | Kind | Status | Notes / risk |
|---|---|---|---|
| **YouTube** | long | Selectors adapted from a mature, MIT-licensed reference implementation (`social-auto-upload`) that has real production mileage. Most likely to work with minimal changes. | Studio's DOM (`ytcp-*` custom elements) still drifts over time; requires `channel="chrome"` (not headless-shell) per upstream's finding. Region-blocked? Set `YT_PROXY`. |
| **TikTok** | short | Original implementation (no reference available for the current TikTok Studio DOM). Scheduling flow is a documented **stub** — `_apply_schedule()` switches to the "schedule" radio but the date/time picker interaction needs to be filled in against the live UI. | Highest risk of stale selectors of the three; `selectors.py` is the one file to fix first. Pin-comment dropback step is best-effort/non-fatal. |
| **X / Twitter** | short | Original implementation, following X's own `data-testid` convention for the core compose flow (comparatively stable). | The "pin a reply to the top" analog (X's reply "Highlight" feature) is flagged in `selectors.py` as fragile/best-effort — X doesn't have a first-class pinned-comment feature like YouTube/TikTok, so the primary dropback channel for X is the link **inside the tweet text itself**, not the reply. |

All three: **no accounts were logged into and no content was published**
while building this, per the task's explicit instructions. Every adapter's
`publish()` is gated by `PlatformAdapter._require_cookie()`, which raises
`NeedsLoginError` *before* importing patchright or touching the network if
`/data/accounts/<platform>/<account>.json` doesn't exist or isn't
recognizable as a Playwright `storage_state` — this is also what makes the
self-tests runnable without a browser installed at all (see below).

### Selector maintenance

Every platform's CSS/testid selectors live in exactly one file —
`platforms/<name>/selectors.py` — never inline in `adapter.py`. When a
platform changes its UI, that's the one file to open. Every automation step
goes through a small set of helpers in `platforms/browser.py`
(`click_step`, `fill_step`, `set_files_step`, `wait_visible_step`) that
raise `StepFailedError(platform, step, selector, cause)` on failure — so an
error always says exactly **which named step** and **which selector** broke,
e.g.:

```
StepFailedError: [tiktok] step 'fill caption' failed (selector: div[data-e2e="video-caption"] div[contenteditable="true"], div.public-DraftEditor-content[contenteditable="true"]): Timeout 15000ms exceeded.
```

Non-critical steps (thumbnails, playlists, tags, pinned comments) use the
non-fatal `click_if_present()` instead, and log a warning rather than
aborting the whole publish — matching the reference project's own treatment
of "nice to have but not blocking" steps.

## Growth loop (dropback), briefly

1. A language's long-form YouTube video publishes; its watch URL is
   reported back (`status: published`).
2. Only then does that language's shorts (`kind: short`, `parent_pub_id`
   pointing at the long) become eligible — enforced by the control plane
   server-side in pull mode, and by `pipeline/dropback.py` +
   `PublisherPipeline` in local-queue mode.
3. When publishing a short, `pipeline/dropback.py::inject_into_description`
   appends the parent's URL to the description/caption/tweet text
   (idempotently — safe to re-run). Platform adapters that support it
   (TikTok, X) additionally attempt to post + pin/highlight that link as a
   **top comment/reply** after the short goes live — best-effort, never
   fatal to the publish itself.

This repeats per language (`zh`/`en`/`ja` today) without any language ever
being hardcoded in the pipeline — every language-specific string comes from
data (`Publication.lang`, the `languages` registry), not code.

## Development / tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

59 tests, all runnable **without patchright/a browser installed** (every
platform import is lazy — see `platforms/browser.py`'s module docstring)
and without any network access. Notably:

- `tests/test_local_runner_e2e.py` — the actual end-to-end self-test
  described in this project's brief: builds a real (ffmpeg-generated) fake
  `.mp4`, a queue with one long + two dependent shorts, **zero cookie
  files**, and runs the real `PublisherPipeline` with the real YouTube/
  TikTok/X adapters. Asserts: the long reaches its adapter and stops at
  `NeedsLoginError`; the shorts are correctly gated (`waiting on parent`)
  and never attempted; once the long is manually marked `published`, the
  shorts unblock and *also* correctly reach their own login gate.
- `tests/test_cli_queue_smoke.py` — same scenario, but through the actual
  `dusk-publisher queue run` Typer command (`CliRunner`), asserting exit
  code `0` and the exact "needs login" message a real user would see.
- `tests/test_dropback_injection.py` — substitutes a recording stub adapter
  to prove the parent's URL is actually threaded into the `PublishRequest`
  once a short is unblocked (real routing/gating/asset-resolution, only the
  final network/browser call is stubbed).
- `tests/test_local_source.py`, `test_account_router.py`, `test_cookies.py`,
  `test_dropback.py`, `test_models.py` — focused unit coverage for the
  local queue file (idempotent report/persist, asset resolution),
  account routing/fallback, cookie-file sanity checks, dropback helpers,
  and the `Publication` model.
- `tests/test_runner_denormalized_parent_url.py` — proves the pull-mode
  dropback fix: a control-plane-shaped fake source (deliberately has no
  `all_publications()`) with a short carrying `parent_url` pre-set gets it
  threaded into the `PublishRequest`, and the defensive fallback (missing
  `parent_url` on a gated row) still publishes, just without a link.
- `tests/test_control_plane.py` — `cover_url` download on `/ops/claim`
  (present / absent / failing-non-fatally), `/ops/heartbeat`'s `health`
  passthrough, and `last_request_ok`/`last_request_error` bookkeeping
  exercised through a real `httpx.MockTransport` round-trip (not mocked
  at the method level) so the actual request/response-handling code runs.
- `tests/test_health.py`, `tests/test_healthz_server.py` — every individual
  probe (disk/cookie-volume for real via `tmp_path`; the browser-launch
  probe against the *real* "patchright not installed" path in this
  sandbox, not a simulation of it), `HealthState`'s `ok`/`last_error`
  aggregation and priority order, the exact `/ops/heartbeat` payload shape,
  and the real `/healthz` HTTP server (actual socket, actual 200/503/404).

`scripts/make_fake_video.py` generates the tiny real `.mp4`/`.jpg` used in
tests and for manual local-queue trials (falls back to a non-playable
placeholder file if `ffmpeg` isn't on `PATH` — fine for these tests, since
none of them ever open the file in a browser).

Docker build/compose were still **not** verified in the environment this
was built in (`docker.sock` access denied there); see
[Health checks & monitoring](#health-checks--monitoring)'s "first
deployment self-check checklist" for exactly how to verify a real build
yourself. What *was* verified manually here without Docker: ran the real
`dusk-publisher daemon run` process directly (not in a container) against
an intentionally unreachable control plane, confirmed it survived the
failure and kept looping, and confirmed `/healthz` correctly reported the
real "patchright not installed" + "connection failed" states as `503`
with specific `last_error` text — see that section for the full output.

## Credits & License

MIT — see [LICENSE](LICENSE).

This project's browser-automation approach — cookie-file-per-account,
`login`/`check`/publish shape, the "why not the official API" rationale for
YouTube, and the "wait for 100% before publishing" rule — is adapted from
[`social-auto-upload`](https://github.com/dreammis/social-auto-upload) (MIT,
~14.8k★), and the YouTube adapter in particular ports a substantial part of
its selector set and upload flow from that project. **Full attribution,
including exactly which functions/selectors were adapted from where, is in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).** The X/Twitter adapter is
original — `social-auto-upload` does not support that platform.

Browser automation is driven by
[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)
(MIT), a stealth-hardened drop-in replacement for Playwright's Python API.
