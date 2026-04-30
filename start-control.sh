#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin"
LOG_DIR="logs"
PID_DIR="$LOG_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

if [[ ! -x "$VENV/uvicorn" ]]; then
  echo "Missing $VENV/uvicorn."
  echo "python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn aiofiles psutil"
  exit 1
fi

TAILSCALE_IP=""
if command -v tailscale >/dev/null 2>&1; then
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      TAILSCALE_IP="$line"
      break
    fi
  done < <(tailscale ip -4 2>/dev/null || true)
fi

HOST="${TAILSCALE_IP:-127.0.0.1}"
PORT="7000"
ACTION="${1:-start}"

export BIND_HOST="$HOST"
export PUBLIC_HOST="$HOST"
export HUB_PORT="$PORT"
export RUN_MODE="control"

stop_hub() {
  local file="$PID_DIR/hub.pid"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(<"$file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.3
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi

  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done < <(pgrep -f "$VENV/uvicorn main:app" || true)
}

stop_hub

if [[ "$ACTION" == "--stop" || "$ACTION" == "stop" ]]; then
  echo "Control hub stopped"
  exit 0
fi

"$VENV/uvicorn" main:app --host "$HOST" --port "$PORT" > "$LOG_DIR/hub.log" 2>&1 &
echo "$!" > "$PID_DIR/hub.pid"

echo "Control hub: http://$HOST:$PORT"
