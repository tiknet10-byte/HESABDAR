#!/usr/bin/env bash
# Local (offline) mode: installs on first run, then opens the app in your browser.
set -e
cd "$(dirname "$0")"
if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/dist ]; then
  (cd frontend && npm ci && npm run build)
fi
cd backend && exec .venv/bin/python manage.py run --open "$@"
