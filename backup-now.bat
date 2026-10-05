@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
.venv\Scripts\python manage.py backup
echo Backups are in backend\data\backups
pause
