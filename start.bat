@echo off
chcp 65001 >nul
title HESABDAR - Server (keep this window open)
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  call "%~dp0install.bat"
  exit /b
)
if not exist "frontend\dist\index.html" (
  echo Building the web interface - needs Node.js ...
  pushd frontend && call npm ci && call npm run build && popd
)
rem ---- stop any older HESABDAR still running on port 8000 (e.g. after an update) ----
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do (
  echo Stopping previous HESABDAR process %%p ...
  taskkill /F /PID %%p >nul 2>&1
)
echo.
echo  HESABDAR is running at  http://127.0.0.1:8000
echo  Your browser will open automatically.
echo  Keep this window open while you work. Close it to stop the program.
echo.
cd backend
.venv\Scripts\python manage.py run --open
pause
