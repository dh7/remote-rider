#!/usr/bin/env bash
# Install (enable, but do not start) the rr-tunnel systemd user unit on the
# control host. Starting it and creating DNS are deliberate, separate steps —
# see README "Public access".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

CONFIG="$HOME/.cloudflared/remote-rider.yml"
if [[ ! -f "$CONFIG" ]]; then
  echo "missing $CONFIG — copy deploy/cloudflared-remote-rider.yml.example there first" >&2
  exit 1
fi

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cp "$ROOT/deploy/rr-tunnel.service" "$UNIT_DIR/rr-tunnel.service"
systemctl --user daemon-reload
systemctl --user enable rr-tunnel.service
echo "Enabled rr-tunnel. Start with: systemctl --user start rr-tunnel   (kill switch: stop)"
