# GitHub Actions publisher runner

The workflow runs a bounded batch every day at 00:00 UTC (08:00 in China) and
can also be started manually. The job runs on `ubuntu-latest`. It installs
Mozilla Firefox and Xvfb on that machine, then runs `dusk-publisher batch run`
there. This workflow does not call the VPS and does not SSH anywhere.

Firefox is the browser. It runs on the Actions machine, never on the VPS.
When an account has a private Zero Trust proxy hostname, the publisher runs
`cloudflared access tcp` with `CF_ACCESS_CLIENT_ID` and
`CF_ACCESS_CLIENT_SECRET` and forwards that hostname to `127.0.0.1` before
Firefox starts. Firefox then uses `socks5://<user>@127.0.0.1:<local port>`.
A public DNS name that answers on 443 is the Access edge, not a direct SOCKS
server, so it still uses that tunnel even if the stored proxy port also
accepts a TCP handshake. A public http proxy, or a SOCKS hostname whose
proxy port accepts TCP and whose port 443 does not, is passed to Firefox
with no tunnel. An account
with no proxy keeps the Actions network. The workflow does not set
`YT_PROXY`, `X_PROXY`, `TIKTOK_PROXY`, or `FIREFOX_REMOTE_URL`.

`FIREFOX_REMOTE_URL` still selects the attach path when that variable is set
in a process, including under pytest's exclusion. This workflow does not set
it. `BROWSER_IDENTITY=actions` does not switch to that path.

## Runtime shape

```text
schedule (00:00 UTC) or workflow_dispatch
  -> actions/checkout
  -> pip install .
  -> scripts/install-actions-browser.sh
       Mozilla Firefox (not Ubuntu's snap transitional firefox package)
       xvfb installed, not started
       FIREFOX_EXECUTABLE exported for later steps
  -> BROWSER_IDENTITY=actions, BROWSER_FAMILY=firefox, DISPLAY=:99, HEADLESS=false
  -> DATA_DIR is a temp directory for this job
  -> dusk-publisher batch run
       syncs ready account storage_state files from Account Vault
       writes $DATA_DIR/firefox-fingerprint.yaml when the vault fingerprint
       is an object; a null fingerprint leaves an existing file alone
       starts Xvfb on :99 at the fingerprint screen size (1920x1080 when
       either dimension is missing)
       writes a mode 0600 proxy sidecar next to each cookie file
       launches Firefox headed with those prefs, captured sec-ch-ua headers,
       the account storage_state, and that account's proxy
  -> always: delete DATA_DIR
```

Secrets exist only in the job environment. They are not written into the repo.
Cookie files stay Playwright `storage_state` JSON under `DATA_DIR`. The
fingerprint file is the same shape `load_firefox_fingerprint` already reads.
Neither is uploaded as an Actions artifact. The cleanup step deletes
`DATA_DIR` even when the batch fails.

There is no local Firefox profile on the runner. A missing cookie file still
stops that account with `NeedsLoginError`. Sign-in state comes from Account
Vault.

The install script only installs packages. Screen size is known after the
fingerprint download, so the publisher starts Xvfb, not the install script.
When `device_pixel_ratio` is greater than 0, the framebuffer is the captured
CSS size times that ratio. Firefox also gets `layout.css.devPixelsPerPx`, so
`window.screen` CSS pixels match the capture.

## Required repository secrets

| Secret | Contents |
|---|---|
| `CONTROL_PLANE_URL` | Base URL of the publishing control plane |
| `STUDIO_AGENT_KEY` | Shared `/ops/*` API key |
| `CF_ACCESS_CLIENT_ID` | Cloudflare Access service-token client ID, if Access protects the API |
| `CF_ACCESS_CLIENT_SECRET` | Matching service-token secret |
| `AETHER_VAULT_URL` | Account Vault Worker URL |
| `AETHER_VAULT_RUNNER_TOKEN` | Account Vault runner bearer token |

`CF_ACCESS_*` may be left empty when the control plane is not behind Access.
When Account Vault is behind Access, these headers are also sent to its runner
API. `AGENT_ID` is `github-actions-publisher` for this workflow.

Set text secrets with `gh secret set NAME`, which reads the secret from a
hidden prompt or standard input.

## Running a batch

Open **Actions -> Publisher run -> Run workflow** and choose:

- `max_tasks`: maximum publications claimed in this invocation.
- `max_minutes`: stop claiming new work after this budget; the current upload
  is allowed to finish.
- `platforms`: `all` or a comma-separated subset such as `youtube,tiktok`.

The workflow timeout is larger than the batch budget so the active upload can
finish. Concurrency is one production job at a time; a second manual run
queues instead of cancelling the active job.

## Operational limits

- Account Vault `storage_state` is the login. The Actions machine does not
  keep a Firefox profile between jobs.
- A task should remain safely retryable because the control plane owns claim
  leases and final publication status.
- Platform automation can break when a site changes its UI. A failed account
  is reported as `needs_login` or `failed`; the batch returns nonzero so the
  workflow is visibly red.
- Use this automation only for accounts and content you are authorized to
  operate, within platform rules and GitHub Actions acceptable-use terms.
