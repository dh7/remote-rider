#!/usr/bin/env bash
set -euo pipefail

SESSION="${1:-1}"

if [[ ! "$SESSION" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  SESSION="1"
fi

exec tmux new-session -A -s "$SESSION"
