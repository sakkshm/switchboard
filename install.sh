#!/usr/bin/env bash
# Install Switchboard: a venv, an editable install, and `switchboard` on PATH.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

step() { printf '\n==> %s\n' "$1"; }
ok() { printf '    %s\n' "$1"; }

step "Checking Python"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "    switchboard needs Python 3.11 or newer." >&2
  echo "    '$PYTHON' is $("$PYTHON" -V 2>&1 || echo missing). Set PYTHON=/path/to/python3.11 and retry." >&2
  exit 1
fi
ok "$("$PYTHON" -V)"

step "Creating the virtualenv in .venv"
if [ -x .venv/bin/python ]; then
  ok "already exists, reusing it"
else
  "$PYTHON" -m venv .venv
  ok "created"
fi

step "Installing Switchboard (editable) and dev dependencies"
.venv/bin/pip install -q -U pip
PYTHONWARNINGS=ignore .venv/bin/pip install -q -e ".[dev]"
ok "installed $(.venv/bin/python -c 'import switchboard, importlib.metadata as m; print("switchboard", m.version("switchboard"))')"

step "Linking the switchboard command into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sf "$PWD/.venv/bin/switchboard" "$BIN_DIR/switchboard"
ok "$BIN_DIR/switchboard -> $PWD/.venv/bin/switchboard"
ok "(a symlink, so edits and git pull take effect immediately)"

if .venv/bin/switchboard --help >/dev/null 2>&1; then
  ok "command works"
else
  echo "    the installed command did not run; please report this" >&2
  exit 1
fi

printf '\n'
if case ":$PATH:" in *":$BIN_DIR:"*) true ;; *) false ;; esac; then
  echo "Done. Next: switchboard status"
else
  echo "Done, but $BIN_DIR is not on your PATH yet. Add it:"
  echo "  export PATH=\"$BIN_DIR:\$PATH\"   # append this line to ~/.bashrc"
  echo "Then: switchboard status"
fi
