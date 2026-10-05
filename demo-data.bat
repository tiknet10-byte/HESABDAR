@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
echo Loading 120 days of sample data (only for testing - do not use on real data)...
.venv\Scripts\python manage.py demo
pause
