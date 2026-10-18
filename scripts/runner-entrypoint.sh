#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/deploy/runner/compose.yaml"
runtime_dir="$repo_root/runtime"

mkdir -p "$runtime_dir/data/accounts" "$runtime_dir/data/cache" "$runtime_dir/warp"

if [[ ! -s "$runtime_dir/accounts.yaml" ]]; then
  echo "runtime/accounts.yaml is missing; restore the runtime bundle before starting" >&2
  exit 2
fi

if [[ ! -s "$runtime_dir/warp/mdm.xml" ]]; then
  echo "runtime/warp/mdm.xml is missing; inject the Zero Trust MDM profile before starting" >&2
  exit 2
fi

cleanup() {
  docker compose -f "$compose_file" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose -f "$compose_file" pull
docker compose -f "$compose_file" up -d warp-proxy

trace="$(docker compose -f "$compose_file" run --rm network-check)"
if ! grep -q '^warp=on$' <<<"$trace"; then
  echo "Cloudflare WARP preflight failed" >&2
  exit 3
fi

echo "Cloudflare WARP preflight passed"
docker compose -f "$compose_file" run --rm publisher
