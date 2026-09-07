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
│   │   └── daemon.py        infinite poll loop for pull mode
│   └── cli.py                `dusk-publisher ...` (Typer)
├── config/                  accounts.example.yaml, local_queue.example.yaml
├── scripts/make_fake_video.py
├── tests/                   see "Development" below
├── Dockerfile / docker-compose.yml
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

`dusk-publisher daemon run` then loops:
`GET /ops/work` → for each returned publication, `POST /ops/claim` (get a
signed asset URL, download it) → publish via the matching platform adapter
→ `POST /ops/report` → periodically `POST /ops/heartbeat` with every
configured account's `check()` result, so the control plane can surface
"XX account needs re-login" to a human.

### Shared contract v1 (consumed as-is, not owned by this project)

```
GET  /ops/work?agent=<id>   -> {"publications": [...], "languages": {...}}
POST /ops/claim             {"pub_id", "agent"} -> {"ok", "asset_url", "pub"}
POST /ops/report            {"pub_id", "status": "published"|"failed", "url", "error"}
POST /ops/heartbeat         {"agent", "accounts": [{"platform","name","cookie_ok"}]}
```
Auth header on every request: `X-Studio-Agent-Key: <STUDIO_AGENT_KEY>`.
401 body: `{"ok": false, "error": "unauthorized"}`.

Publication record fields: `pub_id, story_id, episode_code, lang, platform
(youtube|tiktok|x), account, kind (long|short), asset_key, cover_key,
title, description, tags, status, url, parent_pub_id, scheduled_at,
published_at, error`.

### ⚠️ Contract gaps found while implementing (flagged, not fixed unilaterally)

1. **No signed URL for `cover_key`.** `/ops/claim`'s documented response
   only has a single `asset_url` (for the video). In pull mode,
   `ControlPlaneSource.claim()` therefore always returns `cover_path=None`
   — covers/thumbnails are simply not uploaded in pull mode right now. Local
   queue mode is unaffected (it reads `cover_key` straight off disk).
   **Suggested fix:** either a second signed URL field (e.g. `cover_url`)
   on the `/ops/claim` response, or a generic "give me a signed GET for this
   R2 key" endpoint.
2. **No way to resolve a short's `parent_url` in pull mode.** The dropback
   feature needs the *URL* of the long-form video a short promotes.
   `/ops/work` only returns `status=approved` rows, and by the time a short
   is released the parent is `status=published` — so the parent is never
   in the same `/ops/work` response the short is in, and there's no
   `/ops/pub/<id>` lookup to fetch it separately. Right now,
   `PublisherPipeline` detects this (`ControlPlaneSource` has no
   `all_publications()`), logs a warning, and publishes the short **without**
   an injected link — i.e. the growth-loop's core mechanic is currently a
   no-op in pull mode until this is resolved. Local queue mode has no such
   gap (it can see every record, any status, so it resolves this itself).
   **Suggested fix (simplest):** denormalize — have the control plane
   include the resolved `parent_url` directly on the short's publication
   record in `/ops/work`, e.g. as `parent_url` or by copying `url` from the
   parent when it's already known. That avoids adding a new endpoint.
3. `PublisherPipeline` currently reports `status: "failed"` (not a distinct
   "needs_login" status) via `/ops/report` when an account has no valid
   cookie, with the human-readable reason in `error`. If a dedicated status
   value would be more useful to the control plane's UI (vs. parsing
   `error`), that's a one-line change on this side — flagging so it's a
   joint decision rather than a silent assumption.

None of the above blocks local queue mode. #2 (no parent URL) is the one
that matters most: it means dropback — this whole project's stated growth
mechanic — doesn't actually inject the link yet when driven by the control
plane, only in local-queue mode. Fixing it needs a decision on the C line
(or here, once told which direction to go).

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

34 tests, all runnable **without patchright/a browser installed** (every
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

`scripts/make_fake_video.py` generates the tiny real `.mp4`/`.jpg` used in
tests and for manual local-queue trials (falls back to a non-playable
placeholder file if `ffmpeg` isn't on `PATH` — fine for these tests, since
none of them ever open the file in a browser).

Docker build/compose were **not** verified in the environment this was
built in (no daemon access there); `docker-compose.yml`'s YAML was
validated for syntax, and the `Dockerfile` follows the standard
`mcr.microsoft.com/playwright/python` base + `patchright install chrome`
pattern. Worth a real build/smoke-run before relying on it in production.

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
