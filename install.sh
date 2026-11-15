#!/usr/bin/env bash
# Install Switchboard: a venv, an editable install, and `switchboard` on PATH.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

"$PYTHON" -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -e ".[dev]"

# Symlink the entry point out of the venv so `switchboard` works without activation.
# A symlink (not a copy) so `git pull` and code edits take effect immediately.
mkdir -p "$BIN_DIR"
ln -sf "$PWD/.venv/bin/switchboard" "$BIN_DIR/switchboard"
echo "installed: $BIN_DIR/switchboard -> $PWD/.venv/bin/switchboard"

case ":$PATH:" in
  *":$BIN_DIR:"*) echo "switchboard is on your PATH. Try: switchboard status" ;;
  *)
    echo "add it to your PATH, then run: switchboard status"
    echo "  export PATH=\"$BIN_DIR:\$PATH\"   # add this line to ~/.bashrc"
    ;;
esac
