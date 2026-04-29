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

port_busy() {
  python3 - "$HOST" "$1" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    sys.exit(0)
finally:
    sock.close()
sys.exit(1)
PY
}

pick_free_port() {
  local candidate="$1"
  while port_busy "$candidate"; do
    candidate=$((candidate + 1))
  done
  printf '%s' "$candidate"
}

choose_port() {
  local label="$1"
  local requested="$2"
  shift 2
  local used_ports=("$@")
  local selected="$requested"
  local conflict

  while true; do
    selected="$(pick_free_port "$selected")"
    conflict=0
    for used in "${used_ports[@]}"; do
      if [[ -n "$used" && "$selected" == "$used" ]]; then
        conflict=1
        selected=$((selected + 1))
        break
      fi
    done
    if [[ "$conflict" == "0" ]]; then
      break
    fi
  done

  if [[ "$selected" != "$requested" ]]; then
    echo "  $label port $requested busy, using $selected" >&2
  fi
  printf '%s' "$selected"
}

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

stop_matching() {
  local pattern="$1"
  local pids=""
  pids="$(pgrep -f "$pattern" || true)"
  if [[ -z "$pids" ]]; then
    return
  fi

  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.2
      kill -9 "$pid" 2>/dev/null || true
    fi
  done <<< "$pids"
}

echo "Restarting remote services on $HOST..."
stop_pidfile ttyd
stop_pidfile monitor
stop_pidfile logs
stop_pidfile fileserver
stop_pidfile hub

# Cleanup old remote-rider processes in case pid files are missing.
stop_matching "ttyd.*-i $HOST -p $TERM_PORT.*terminal-entry.sh"
stop_matching "$VENV/uvicorn monitor:app --host $HOST --port"
stop_matching "$VENV/uvicorn logs:app --host $HOST --port"
stop_matching "$VENV/uvicorn fileserver:app --host $HOST --port"
stop_matching "$VENV/uvicorn main:app --host $HOST --port"

TERM_PORT="$(choose_port ttyd "$TERM_PORT")"
MONITOR_PORT="$(choose_port monitor "$MONITOR_PORT" "$TERM_PORT")"
LOGS_PORT="$(choose_port logs "$LOGS_PORT" "$TERM_PORT" "$MONITOR_PORT")"
FILES_PORT="$(choose_port files "$FILES_PORT" "$TERM_PORT" "$MONITOR_PORT" "$LOGS_PORT")"
HUB_PORT="$(choose_port hub "$HUB_PORT" "$TERM_PORT" "$MONITOR_PORT" "$LOGS_PORT" "$FILES_PORT")"

export TERM_PORT MONITOR_PORT LOGS_PORT FILES_PORT

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
