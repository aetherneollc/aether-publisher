# Third-Party Notices

`dusk-publisher` is an independent project, but its design for browser-automation
video publishing was directly inspired by, and in places ported/adapted from,
the following open-source project:

## social-auto-upload

- Repository: https://github.com/dreammis/social-auto-upload
- License: MIT
- Stars (at time of writing): ~14.8k

`social-auto-upload` pioneered the cookie-file-per-account browser automation
pattern for unattended multi-platform video publishing, driven by
[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) (a
stealth-hardened drop-in replacement for Playwright). We reuse the following
ideas and, in the YouTube adapter, substantial portions of the *approach and
selector set* (adapted to our own module structure and error handling):

1. **YouTube must be published via Studio browser automation, not the official
   Data API.** Videos uploaded through an unaudited Google API project are
   force-locked to "private" and cannot be made public without passing
   Google's compliance audit — impractical for a personal/single channel.
   Browser automation has no such restriction.
   See: `uploader/youtube_uploader/main.py` in the upstream repo, and the
   corresponding README section ("YouTube 说明").
2. **Wait for the upload progress bar to reach 100% before clicking
   Publish.** Browser-driven uploads stream while the tab stays open; clicking
   "Publish" (and/or closing the browser) before the upload finishes truncates
   the upload and leaves it stuck mid-progress. See `_wait_upload_complete()`
   in the same file, which we adapted as `wait_for_upload_complete()` in
   [`src/dusk_publisher/platforms/youtube/adapter.py`](src/dusk_publisher/platforms/youtube/adapter.py).
3. **Cookie storage via Playwright/patchright `storage_state` JSON, one file
   per account**, with a `login` (interactive) / `check` (headless
   revalidation) / `upload` (or here, `publish`) three-command shape per
   platform. See `uploader/youtube_uploader/main.py`'s `youtube_cookie_gen`,
   `cookie_auth`, and `youtube_setup` functions, adapted as
   `login()` / `check()` / `publish()` on our `PlatformAdapter` base class.
4. Several concrete YouTube Studio CSS/element selectors (e.g.
   `#title-textarea #textbox`, `tp-yt-paper-radio-button[name='PUBLIC']`,
   `#done-button`, the playlist-dialog flow) are adapted from the upstream
   `YouTubeVideo.upload()` method into
   [`src/dusk_publisher/platforms/youtube/selectors.py`](src/dusk_publisher/platforms/youtube/selectors.py)
   and the corresponding steps in `adapter.py`.

The upstream project does **not** implement an X/Twitter uploader (it targets
mainly Chinese platforms plus TikTok and YouTube); the `x` adapter in this
project is our own original implementation, following the same
login/check/publish shape and cookie convention for consistency, but with no
code ported from upstream.

Per `social-auto-upload`'s own contribution guidelines, third-party-derived
code must carry its origin and license — this file, plus inline attribution
comments at the top of the affected modules, serve that purpose.

`social-auto-upload`'s own Bilibili support is in turn built on
[`biliup`](https://github.com/biliup/biliup); we do not use Bilibili, so that
dependency chain does not apply here.

## patchright

- Repository: https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python
- License: MIT (Apache-2.0 for the upstream Chromium patches where applicable — see that repo)

Used directly as our browser automation driver (drop-in replacement for
Playwright's Python API). See `requirements.txt` / `pyproject.toml`.

## cloudflare-warp-proxy

- Repository: https://github.com/seiry/docker-warp-proxy
- License: MIT

Used as an external container image by `deploy/runner/compose.yaml` to expose a
Cloudflare WARP / Zero Trust connection as a local SOCKS proxy. No source code
from that repository is vendored here.

## Other Python dependencies

See `pyproject.toml` for the full list (Typer, Pydantic, httpx, PyYAML, etc.),
each under its own OSI-approved license (BSD/MIT/Apache-2.0). No other
third-party source code is vendored into this repository.
