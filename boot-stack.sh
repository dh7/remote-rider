#!/usr/bin/env bash
# Boot entrypoint for the Remote Rider stack, invoked by the systemd user unit
# (deploy/remote-rider.service). It waits for Tailscale, then starts the right
# mode for this machine:
#   - the control host (fixed Tailscale IP) runs control + remote  => RUN_MODE=all
#   - every other machine runs the remote node stack only          => RUN_MODE=remote
set -euo pipefail
cd "$(dirname "$0")"

# Fixed control host — see Agents.md "Where Control Runs". Override via RR_CONTROL_IP.
CONTROL_IP="${RR_CONTROL_IP:-100.119.43.10}"

# Wait for the Tailscale IPv4 to be assigned (up to ~120s) before binding services.
IP=""
for _ in $(seq 1 60); do
  IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  [ -n "$IP" ] && break
  sleep 2
done

if [ -n "$IP" ] && [ "$IP" = "$CONTROL_IP" ]; then
  echo "[boot-stack] control host detected ($IP) — starting control hub first"
  ./start-control.sh
  sleep 2
fi

echo "[boot-stack] starting remote node stack"
exec ./start-remote.sh
