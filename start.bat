@echo off
REM Local (offline) mode for Windows: installs on first run, then opens the app.
cd /d %~dp0
if not exist backend\.venv (
  python -m venv backend\.venv
  backend\.venv\Scripts\pip install -q -r backend\requirements.txt
)
if not exist frontend\dist (
  cd frontend && call npm ci && call npm run build && cd ..
)
cd backend
.venv\Scripts\python manage.py run --open
