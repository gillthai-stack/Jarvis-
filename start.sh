#!/usr/bin/env bash
# Sets up Jarvis on first run, then starts it and opens the dashboard.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: creating a Python environment…"
  python3 -m venv .venv
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

PORT=$(grep -E '^JARVIS_PORT=' .env | cut -d= -f2)
PORT=${PORT:-8765}
(sleep 2 && open "http://127.0.0.1:${PORT}") >/dev/null 2>&1 &
exec .venv/bin/python -m jarvis
