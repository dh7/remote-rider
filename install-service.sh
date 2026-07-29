#!/usr/bin/env bash
# Install the Remote Rider boot service (systemd user unit) on THIS machine so the
# stack comes back automatically after a reboot. Idempotent — safe to re-run.
# Works regardless of where the repo lives (the unit is pinned to this path).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# systemctl --user needs a runtime dir; set it if the (non-interactive) env lacks it.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# 1) byobu prerequisite: disable tmux mouse mode (byobu enables it by default,
#    which steals ttyd's copy-on-select). Only where byobu is installed.
if command -v byobu-tmux >/dev/null 2>&1; then
  BK="$HOME/.byobu/keybindings.tmux"
  mkdir -p "$HOME/.byobu"; touch "$BK"
  if ! grep -qE '^[[:space:]]*set(-option)?[[:space:]]+-g[[:space:]]+mouse[[:space:]]+off' "$BK"; then
    echo "set -g mouse off" >> "$BK"
    echo "byobu: added 'set -g mouse off' to $BK"
  fi
fi

# 2) generate + install the systemd user unit, pinned to THIS repo path
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s|%h/code/remote-rider|$ROOT|g" "$ROOT/deploy/remote-rider.service" > "$UNIT_DIR/remote-rider.service"
echo "installed $UNIT_DIR/remote-rider.service (ExecStart=$ROOT/boot-stack.sh)"
systemctl --user daemon-reload

# 3) linger so the unit starts at boot without an active login session
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
  if loginctl enable-linger "$USER" 2>/dev/null; then
    echo "enabled linger for $USER"
  else
    echo "WARNING: could not enable linger; run: sudo loginctl enable-linger $USER"
  fi
fi

systemctl --user enable remote-rider.service
echo
echo "Enabled. Bring the stack up now with: systemctl --user restart remote-rider"
echo "Check status with:                    systemctl --user status remote-rider"
