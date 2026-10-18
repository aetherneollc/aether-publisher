# GitHub Actions publisher runner

The workflow is manual-only and runs a bounded batch. It does not contain a
schedule trigger.

## Runtime shape

```text
workflow_dispatch
  -> restore account map, cookie bundle, and WARP MDM profile
  -> start WARP Zero Trust SOCKS sidecar
  -> verify cdn-cgi/trace reports warp=on
  -> run publisher container
       -> fetch work from the control plane
       -> filter platform and order by account
       -> claim and publish tasks sequentially
       -> report each result
  -> remove plaintext runtime files, even on failure
```

The same Docker composition can run on another runner without changing the
publisher image. GitHub Actions is currently only the orchestrator.

## Required repository secrets

| Secret | Contents |
|---|---|
| `CONTROL_PLANE_URL` | Base URL of the publishing control plane |
| `STUDIO_AGENT_KEY` | Shared `/ops/*` API key |
| `CF_ACCESS_CLIENT_ID` | Cloudflare Access service-token client ID, if Access protects the API |
| `CF_ACCESS_CLIENT_SECRET` | Matching service-token secret |
| `ACCOUNTS_YAML_B64` | Base64 of `runtime/accounts.yaml` |
| `COOKIE_BUNDLE_B64` | Base64 gzip tar of account state files |
| `CF_WARP_MDM_B64` | Base64 of the Zero Trust Linux `mdm.xml` profile |

`CF_ACCESS_*` may be left empty when the control plane is not behind Access.
The WARP profile should use a narrowly scoped Zero Trust service token/device
enrollment policy. Do not use a human Cloudflare password or API global key.
Start from `config/cloudflare-warp.mdm.example.xml`; the service-token device
enrollment policy must use Cloudflare's **Service Auth** action. The pinned
sidecar expects local proxy mode with MASQUE on internal port `40001`.

Generate the first two account secrets with:

```bash
./scripts/capture-cookies.sh
```

Set the WARP profile without printing it to the terminal:

```bash
base64 -w0 path/to/mdm.xml | gh secret set CF_WARP_MDM_B64 \
  --repo aetherneollc/aether-publisher
```

Set the remaining text secrets with `gh secret set NAME --repo ...`, which
reads the secret from a hidden prompt or standard input.

## Running a batch

Open **Actions -> Publisher run -> Run workflow** and choose:

- `max_tasks`: maximum publications claimed in this invocation.
- `max_minutes`: stop claiming new work after this budget; the current upload
  is allowed to finish.
- `platforms`: `all` or a comma-separated subset such as `youtube,tiktok`.

The workflow timeout is intentionally larger than the batch budget to leave
room for the active upload and cleanup. Concurrency is one production job at a
time; a second manual invocation queues instead of cancelling the active job.

## Operational limits

- GitHub-hosted jobs are ephemeral, so local WARP and browser-state mutations
  disappear after the run.
- A task should remain safely retryable because the control plane owns claim
  leases and final publication status.
- Platform automation can break when a site changes its UI. A failed account
  is reported as `needs_login` or `failed`; the batch returns nonzero so the
  workflow is visibly red.
- Use this automation only for accounts and content you are authorized to
  operate, within platform rules and GitHub Actions acceptable-use terms.
- Cloudflare documents a request timeout for Local proxy mode. Before enabling
  real publication, run a long-upload canary for every platform. If resumable
  upload requests are dropped, replace only the network sidecar with a full
  WARP network-namespace runner; the publisher container and batch contract do
  not need to change.
