# Cookie capture and refresh

## Trust boundary

Passwords, passkeys, CAPTCHA responses, and 2FA codes belong only in a visible
browser on a trusted desktop. They must never be placed in GitHub Secrets or
typed into an Actions log. The publisher persists only Patchright browser
`storage_state` JSON: cookies and origin-local storage for an already logged-in
session.

Treat those files exactly like passwords. Anyone who gets a valid state file
may be able to act as the account until the session is revoked.

## One-command flow

Prerequisites: Linux desktop (or a trusted Linux machine with a visible X/VNC
session), Python 3, and optionally an authenticated GitHub CLI.

```bash
cp config/accounts.example.yaml runtime/accounts.yaml
# Edit runtime/accounts.yaml with the account aliases used by the control plane.
gh auth login
./scripts/capture-cookies.sh
```

The command:

1. Creates `.venv` and installs the browser on first use.
2. Opens TikTok, X, and YouTube login pages one at a time.
3. Waits for the operator to finish login and 2FA.
4. Stores each account under
   `runtime/data/accounts/<platform>/<account>.json`.
5. Reopens every account headlessly to verify that the session works.
6. Produces the git-ignored `runtime/cookies.tar.gz`.
7. If `gh` is authenticated and the bundle fits, updates
   `COOKIE_BUNDLE_B64` and `ACCOUNTS_YAML_B64` in the repository.

Refresh a narrower set when one session expires:

```bash
./scripts/capture-cookies.sh --platform youtube
./scripts/capture-cookies.sh --account yt_zh_main
```

Never commit `runtime/`, paste the bundle into an issue, or upload it as an
Actions artifact.

## Why this is not captured inside Actions

Interactive account login on an ephemeral public runner is both awkward and a
weaker trust model. CAPTCHA and 2FA need a human-visible browser, while the
resulting authenticated state should have the smallest possible exposure.
Capture locally, then give the headless publisher only the resulting state.

## GitHub Secret size and the production path

GitHub Secrets have a size ceiling. The helper refuses automatic upload when
the base64 bundle reaches 47,000 bytes. The initial implementation is therefore
best for a modest number of accounts.

For a larger account set, store one encrypted object per account in a private
Cloudflare R2 bucket. Encrypt locally with `age`, keep the decryption identity
as a GitHub environment secret, download only the selected accounts at job
start, and delete plaintext in the unconditional cleanup step. This also makes
individual cookie rotation possible without replacing one monolithic secret.

Browser state refreshed during an ephemeral Actions run is not written back to
GitHub Secrets. If a platform rotates a session, rerun the local capture helper.
The R2 phase should add opt-in encrypted write-back with object versioning.

## Revocation

If a bundle may have leaked, revoke active sessions in each platform's security
settings, remove `COOKIE_BUNDLE_B64` from repository secrets, and capture fresh
sessions. Rotating a password alone does not always invalidate every browser
session.
