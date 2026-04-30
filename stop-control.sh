#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

exec ./start-control.sh --stop
