#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

mkdir -p "$BIN_DIR"

for tool in rr-init rr-files rr-skill rr-notes; do
  ln -sfn "$ROOT/$tool" "$BIN_DIR/$tool"
  echo "linked $BIN_DIR/$tool -> $ROOT/$tool"
done

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "warning: $BIN_DIR is not in PATH for this shell."
    echo "Add this to your shell profile:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac
