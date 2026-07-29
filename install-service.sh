#!/usr/bin/env bash
# Install the Remote Rider boot service (systemd user unit) on THIS machine so the
# stack comes back automatically after a reboot. Idempotent — safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1) byobu prerequisite: disable tmux mouse mode so ttyd's copy-on-select keeps
#    working (byobu turns mouse mode on by default, which steals text selection
#    in the ttyd iframe). Only relevant where byobu is installed.
if command -v byobu-tmux >/dev/null 2>&1; then
  BK="$HOME/.byobu/keybindings.tmux"
  mkdir -p "$HOME/.byobu"
  touch "$BK"
  if ! grep -qE '^[[:space:]]*set(-option)?[[:space:]]+-g[[:space:]]+mouse[[:space:]]+off' "$BK"; then
    echo "set -g mouse off" >> "$BK"
    echo "byobu: added 'set -g mouse off' to $BK"
  fi
fi

# 2) install + enable the systemd user unit
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
install -m 0644 "$ROOT/deploy/remote-rider.service" "$UNIT_DIR/remote-rider.service"
echo "installed $UNIT_DIR/remote-rider.service"
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
