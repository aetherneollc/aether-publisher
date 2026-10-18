#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="$repo_root/runtime"
venv_dir="$repo_root/.venv"
repo_slug="${GITHUB_REPOSITORY:-aetherneollc/aether-publisher}"

mkdir -p "$runtime_dir/data/accounts"
if [[ ! -s "$runtime_dir/accounts.yaml" ]]; then
  echo "Create runtime/accounts.yaml from config/accounts.example.yaml first." >&2
  exit 2
fi

if [[ ! -x "$venv_dir/bin/dusk-publisher" ]]; then
  python3 -m venv "$venv_dir"
  "$venv_dir/bin/pip" install -e "$repo_root"
  "$venv_dir/bin/python" -m patchright install chrome
fi

DATA_DIR="$runtime_dir/data" \
ACCOUNTS_FILE="$runtime_dir/accounts.yaml" \
HEADLESS=0 \
  "$venv_dir/bin/dusk-publisher" accounts capture "$@"

DATA_DIR="$runtime_dir/data" \
ACCOUNTS_FILE="$runtime_dir/accounts.yaml" \
HEADLESS=1 \
  "$venv_dir/bin/dusk-publisher" accounts check-all "$@"

bundle="$runtime_dir/cookies.tar.gz"
tar -C "$runtime_dir/data/accounts" -czf "$bundle" .
bundle_b64_size="$(base64 -w0 "$bundle" | wc -c)"

echo "Cookie capture complete: $bundle"
if (( bundle_b64_size >= 47000 )); then
  echo "Bundle is too large for one GitHub Secret (${bundle_b64_size} bytes after base64)."
  echo "Store it encrypted in private R2 instead."
  exit 0
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  base64 -w0 "$bundle" | gh secret set COOKIE_BUNDLE_B64 --repo "$repo_slug"
  base64 -w0 "$runtime_dir/accounts.yaml" | gh secret set ACCOUNTS_YAML_B64 --repo "$repo_slug"
  echo "Updated COOKIE_BUNDLE_B64 and ACCOUNTS_YAML_B64 in $repo_slug."
else
  echo "Install/login to GitHub CLI to upload automatically, or set COOKIE_BUNDLE_B64 manually."
fi
