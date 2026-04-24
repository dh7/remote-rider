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

echo "Starting all services..."

ttyd -W -i "$BIND_HOST" -p 7681 "$SHELL_CMD" > "$LOG_DIR/ttyd.log" 2>&1 &
echo "  ttyd    -> http://$PUBLIC_HOST:7681"

"$VENV/uvicorn" monitor:app --host "$BIND_HOST" --port 8001 > "$LOG_DIR/monitor.log" 2>&1 &
echo "  monitor -> http://$PUBLIC_HOST:8001"

"$VENV/uvicorn" logs:app --host "$BIND_HOST" --port 8002 > "$LOG_DIR/logs.log" 2>&1 &
echo "  logs    -> http://$PUBLIC_HOST:8002"

"$VENV/uvicorn" fileserver:app --host "$BIND_HOST" --port 8080 > "$LOG_DIR/fileserver.log" 2>&1 &
echo "  files   -> http://$PUBLIC_HOST:8080/files"

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
echo "Stop: pkill -f ttyd; pkill -f uvicorn"
