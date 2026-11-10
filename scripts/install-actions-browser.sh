#!/usr/bin/env bash
# Mozilla Firefox plus Xvfb for the GitHub Actions publisher.
# Ubuntu's firefox package is a snap transitional wrapper and cannot be
# Playwright's moz-firefox. This script only installs packages. Xvfb starts
# later, after the fingerprint file is on disk and the screen size is known.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if [[ "$(id -u)" -eq 0 ]]; then
  run_root() { "$@"; }
else
  run_root() { sudo -E "$@"; }
fi

run_root install -d -m 0755 /etc/apt/keyrings
wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O- | run_root tee /etc/apt/keyrings/packages.mozilla.org.asc >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" | run_root tee /etc/apt/sources.list.d/mozilla.list >/dev/null
run_root tee /etc/apt/preferences.d/mozilla >/dev/null <<'EOF'
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
EOF

run_root apt-get update
run_root apt-get install -y firefox xvfb

installed="$(dpkg-query -W -f '${Version}' firefox)"
case "$installed" in
  *snap*)
    echo "refusing Ubuntu snap transitional firefox ($installed)" >&2
    exit 1
    ;;
esac
if ! apt-cache policy firefox | grep -q 'packages.mozilla.org'; then
  echo "firefox was not installed from packages.mozilla.org" >&2
  exit 1
fi

if [[ -x /usr/lib/firefox/firefox ]]; then
  FIREFOX_EXECUTABLE=/usr/lib/firefox/firefox
elif [[ -x /usr/bin/firefox ]]; then
  FIREFOX_EXECUTABLE=/usr/bin/firefox
else
  echo "Mozilla Firefox executable was not found after install" >&2
  exit 1
fi

export FIREFOX_EXECUTABLE
printf 'export FIREFOX_EXECUTABLE=%q\n' "$FIREFOX_EXECUTABLE"
if [[ -n "${GITHUB_ENV:-}" && -f "${GITHUB_ENV}" ]]; then
  printf 'FIREFOX_EXECUTABLE=%s\n' "$FIREFOX_EXECUTABLE" >> "$GITHUB_ENV"
fi

# cloudflared access tcp runs later, immediately before Firefox, when an
# account proxy host is a private Zero Trust hostname. The service token
# stays in CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET and is not printed.
arch="$(dpkg --print-architecture)"
case "$arch" in
  amd64|arm64) ;;
  *)
    echo "unsupported architecture for cloudflared: $arch" >&2
    exit 1
    ;;
esac
cloudflared_deb="$(mktemp --suffix=.deb)"
wget -q -O "$cloudflared_deb" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
run_root dpkg -i "$cloudflared_deb"
rm -f "$cloudflared_deb"
cloudflared --version
