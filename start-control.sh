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

# Public-access gateway (gateway.py): only on the control host, only when configured.
# It listens on 127.0.0.1 only; cloudflared (rr-tunnel.service) is its only client.
GATEWAY_ENV="${RR_GATEWAY_ENV:-$HOME/.config/remote-rider/gateway.env}"
GATEWAY_PORT="7010"

stop_gateway() {
  local file="$PID_DIR/gateway.pid"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(<"$file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi
  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done < <(pgrep -f "$VENV/uvicorn --factory gateway:create_app_from_env" || true)
}

start_gateway() {
  [[ -f "$GATEWAY_ENV" ]] || return 0
  if [[ "$(stat -c %a "$GATEWAY_ENV")" != "600" ]]; then
    echo "WARNING: $GATEWAY_ENV must be mode 600 — public gateway NOT started" >&2
    return 0
  fi
  (
    set -a
    # shellcheck disable=SC1090
    . "$GATEWAY_ENV"
    set +a
    export RR_HUB_URL="http://$HOST:$PORT"
    exec setsid "$VENV/uvicorn" --factory gateway:create_app_from_env \
      --host 127.0.0.1 --port "$GATEWAY_PORT" --no-access-log --no-server-header
  ) > "$LOG_DIR/gateway.log" 2>&1 < /dev/null &
  echo "$!" > "$PID_DIR/gateway.pid"
  echo "Public gateway: http://127.0.0.1:$GATEWAY_PORT (behind rr-tunnel)"
}

stop_hub
stop_gateway

if [[ "$ACTION" == "--stop" || "$ACTION" == "stop" ]]; then
  echo "Control hub stopped"
  exit 0
fi

# Bind the hub to 0.0.0.0 so clients hitting localhost:$PORT work alongside the
# Tailscale and LAN addresses. $HOST is still advertised as $PUBLIC_HOST.
setsid "$VENV/uvicorn" main:app --host 0.0.0.0 --port "$PORT" > "$LOG_DIR/hub.log" 2>&1 < /dev/null &
echo "$!" > "$PID_DIR/hub.pid"

echo "Control hub: http://$HOST:$PORT (also http://127.0.0.1:$PORT)"

start_gateway
