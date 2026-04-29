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

TTYD_BIN=""
if command -v ttyd >/dev/null 2>&1; then
  TTYD_BIN="$(command -v ttyd)"
elif [[ -x "$VENV/ttyd" ]]; then
  TTYD_BIN="$VENV/ttyd"
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

TERM_PORT="7681"
MONITOR_PORT="8001"
LOGS_PORT="8002"
FILES_PORT="8080"
HUB_PORT="7000"

stop_pidfile() {
  local file="$PID_DIR/$1.pid"
  if [[ ! -f "$file" ]]; then
    return
  fi
  local pid
  pid="$(<"$file")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 0.3
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

assert_port_free() {
  local port="$1"
  python3 - "$HOST" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    print(f"Port {port} is already in use on {host}")
    sys.exit(1)
finally:
    sock.close()
PY
}

echo "Restarting remote services on $HOST..."
stop_pidfile ttyd
stop_pidfile monitor
stop_pidfile logs
stop_pidfile fileserver
stop_pidfile hub

assert_port_free "$TERM_PORT"
assert_port_free "$MONITOR_PORT"
assert_port_free "$LOGS_PORT"
assert_port_free "$FILES_PORT"
assert_port_free "$HUB_PORT"

if [[ -n "$TTYD_BIN" ]]; then
  "$TTYD_BIN" -W -a -i "$HOST" -p "$TERM_PORT" ./terminal-entry.sh > "$LOG_DIR/ttyd.log" 2>&1 &
  echo "$!" > "$PID_DIR/ttyd.pid"
  echo "  ttyd      -> http://$HOST:$TERM_PORT"
else
  : > "$LOG_DIR/ttyd.log"
  echo "  ttyd      -> skipped (not installed)"
fi

"$VENV/uvicorn" monitor:app --host "$HOST" --port "$MONITOR_PORT" > "$LOG_DIR/monitor.log" 2>&1 &
echo "$!" > "$PID_DIR/monitor.pid"
echo "  monitor   -> http://$HOST:$MONITOR_PORT"

"$VENV/uvicorn" logs:app --host "$HOST" --port "$LOGS_PORT" > "$LOG_DIR/logs.log" 2>&1 &
echo "$!" > "$PID_DIR/logs.pid"
echo "  logs      -> http://$HOST:$LOGS_PORT"

"$VENV/uvicorn" fileserver:app --host "$HOST" --port "$FILES_PORT" > "$LOG_DIR/fileserver.log" 2>&1 &
echo "$!" > "$PID_DIR/fileserver.pid"
echo "  files     -> http://$HOST:$FILES_PORT/files"

"$VENV/uvicorn" main:app --host "$HOST" --port "$HUB_PORT" > "$LOG_DIR/hub.log" 2>&1 &
echo "$!" > "$PID_DIR/hub.pid"
echo "  api/hub   -> http://$HOST:$HUB_PORT"

echo ""
echo "Remote stack ready on $HOST"
