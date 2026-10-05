@echo off
chcp 65001 >nul
rem Run this after extracting a new version over the old folder.
cd /d "%~dp0"
echo Stopping running HESABDAR...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do taskkill /F /PID %%p >nul 2>&1
echo Updating packages...
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt -q
call "%~dp0start.bat"
