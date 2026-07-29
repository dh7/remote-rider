#!/usr/bin/env bash
set -euo pipefail

SESSION="${1:-1}"

if [[ ! "$SESSION" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  SESSION="1"
fi

# Prefer byobu (status bar + F-key keypad) so the tmux server boots with byobu's
# profile loaded from the start. Fall back to bare tmux on machines without byobu.
# NOTE: byobu's profile only applies when tmux STARTS THE SERVER, so switching a
# running plain-tmux server to byobu requires `tmux kill-server` (or a reboot).
if command -v byobu-tmux >/dev/null 2>&1; then
  exec byobu-tmux new-session -A -s "$SESSION"
fi

exec tmux new-session -A -s "$SESSION"
