#!/usr/bin/env bash
# Sets up Jarvis on first run, then starts it and opens the dashboard.
set -euo pipefail
cd "$(dirname "$0")"

# Jarvis needs Python 3.10 or newer. macOS ships 3.9, so look for a newer one first.
is_new_enough() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 \
      /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
      /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && is_new_enough "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

# Rebuild the environment if it was made with a Python that is too old.
if [ -d .venv ] && ! is_new_enough .venv/bin/python; then
  echo "The existing Python environment is too old, rebuilding it…"
  rm -rf .venv
fi

if [ ! -d .venv ]; then
  if ! PYTHON=$(find_python); then
    echo "Jarvis needs Python 3.10 or newer, but this Mac has $(python3 --version 2>&1 || echo 'no Python')."
    echo "Install Python 3.12 from https://www.python.org/downloads/macos/ then run ./start.sh again."
    exit 1
  fi
  echo "First run: creating a Python environment with $("$PYTHON" --version)…"
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
fi
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env. Paste your Anthropic API key into it, then run ./start.sh again:"
  echo "  open -e .env"
  exit 1
fi

PORT=$(grep -E '^JARVIS_PORT=' .env | cut -d= -f2 || true)
PORT=${PORT:-8765}
(sleep 2 && open "http://127.0.0.1:${PORT}") >/dev/null 2>&1 &
exec .venv/bin/python -m jarvis
