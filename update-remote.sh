#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

BRANCH="${1:-main}"
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/update-remote.log"
mkdir -p "$LOG_DIR"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] update-remote start branch=$BRANCH"
  git pull --ff-only origin "$BRANCH"
  ./start-remote.sh
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] update-remote complete branch=$BRANCH"
} >> "$LOG_FILE" 2>&1
