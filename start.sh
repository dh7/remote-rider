#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

if [[ ! -x "$VENV/uvicorn" ]]; then
  echo "Missing $VENV/uvicorn. Create venv and install deps first."
  echo "python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn aiofiles psutil"
  exit 1
fi

SHELL_CMD="${SHELL:-/bin/bash}"
if [[ ! -x "$SHELL_CMD" ]]; then
  SHELL_CMD="/bin/bash"
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

BIND_HOST="${BIND_HOST:-${TAILSCALE_IP:-127.0.0.1}}"
PUBLIC_HOST="${PUBLIC_HOST:-$BIND_HOST}"
HUB_PORT="${HUB_PORT:-7000}"
TERM_PORT="${TERM_PORT:-7681}"
MONITOR_PORT="${MONITOR_PORT:-8001}"
LOGS_PORT="${LOGS_PORT:-8002}"
FILES_PORT="${FILES_PORT:-8080}"

port_busy() {
  python3 - "$BIND_HOST" "$1" <<'PY'
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
  local name="$1"
  local requested="$2"
  shift 2
  local used_ports=("$@")
  local selected
  local has_conflict
  selected="$requested"

  while true; do
    selected="$(pick_free_port "$selected")"
    has_conflict=0
    for used in "${used_ports[@]}"; do
      if [[ -n "$used" && "$selected" == "$used" ]]; then
        has_conflict=1
        selected=$((selected + 1))
        break
      fi
    done
    if [[ "$has_conflict" == "0" ]]; then
      break
    fi
  done

  if [[ "$selected" != "$requested" ]]; then
    echo "  $name port $requested busy, using $selected" >&2
  fi
  printf '%s' "$selected"
}

TERM_PORT="$(choose_port ttyd "$TERM_PORT")"
MONITOR_PORT="$(choose_port monitor "$MONITOR_PORT" "$TERM_PORT")"
LOGS_PORT="$(choose_port logs "$LOGS_PORT" "$TERM_PORT" "$MONITOR_PORT")"
FILES_PORT="$(choose_port files "$FILES_PORT" "$TERM_PORT" "$MONITOR_PORT" "$LOGS_PORT")"
HUB_PORT="$(choose_port hub "$HUB_PORT" "$TERM_PORT" "$MONITOR_PORT" "$LOGS_PORT" "$FILES_PORT")"

export TERM_PORT MONITOR_PORT LOGS_PORT FILES_PORT

TTYD_BIN="${TTYD_BIN:-}"
if [[ -z "$TTYD_BIN" ]]; then
  if command -v ttyd >/dev/null 2>&1; then
    TTYD_BIN="$(command -v ttyd)"
  elif [[ -x "$VENV/ttyd" ]]; then
    TTYD_BIN="$VENV/ttyd"
  fi
fi

if [[ -z "$TTYD_BIN" ]]; then
  export DISABLE_TERMINAL=1
else
  export DISABLE_TERMINAL=0
fi

echo "Starting all services..."

if [[ "$DISABLE_TERMINAL" == "0" ]]; then
  TERM_CMD=("$SHELL_CMD")
  TERM_BACKEND="shell"
  TERM_URLARG_FLAG=()

  if command -v tmux >/dev/null 2>&1; then
    TERM_BACKEND="tmux"
    TERM_CMD=("./terminal-entry.sh")
    TERM_URLARG_FLAG=("-a")
  else
    echo "  tmux    -> not found, falling back to plain shell" >&2
  fi

  "$TTYD_BIN" -W "${TERM_URLARG_FLAG[@]}" -i "$BIND_HOST" -p "$TERM_PORT" "${TERM_CMD[@]}" > "$LOG_DIR/ttyd.log" 2>&1 &
  echo "  ttyd    -> http://$PUBLIC_HOST:$TERM_PORT"
  if [[ "$TERM_BACKEND" == "tmux" ]]; then
    echo "  tmux    -> default session '1' (or set per URL: ?arg=<session>)"
  fi
else
  : > "$LOG_DIR/ttyd.log"
  echo "  ttyd    -> skipped (ttyd not installed)"
fi

"$VENV/uvicorn" monitor:app --host "$BIND_HOST" --port "$MONITOR_PORT" > "$LOG_DIR/monitor.log" 2>&1 &
echo "  monitor -> http://$PUBLIC_HOST:$MONITOR_PORT"

"$VENV/uvicorn" logs:app --host "$BIND_HOST" --port "$LOGS_PORT" > "$LOG_DIR/logs.log" 2>&1 &
echo "  logs    -> http://$PUBLIC_HOST:$LOGS_PORT"

"$VENV/uvicorn" fileserver:app --host "$BIND_HOST" --port "$FILES_PORT" > "$LOG_DIR/fileserver.log" 2>&1 &
echo "  files   -> http://$PUBLIC_HOST:$FILES_PORT/files"

"$VENV/uvicorn" main:app --host "$BIND_HOST" --port "$HUB_PORT" > "$LOG_DIR/hub.log" 2>&1 &
echo "  hub     -> http://$PUBLIC_HOST:$HUB_PORT"

sleep 1

if command -v xdg-open >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
  xdg-open "http://$PUBLIC_HOST:$HUB_PORT" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "http://$PUBLIC_HOST:$HUB_PORT" >/dev/null 2>&1 || true
fi

echo ""
echo "Hub:  http://$PUBLIC_HOST:$HUB_PORT"
if [[ "$DISABLE_TERMINAL" == "1" ]]; then
  echo "Note: Terminal tab disabled (install ttyd to enable)."
fi
echo "Stop: pkill -f ttyd; pkill -f uvicorn"
