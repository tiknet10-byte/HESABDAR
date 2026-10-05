@echo off
chcp 65001 >nul
setlocal
title HESABDAR - Install
cd /d "%~dp0"
echo.
echo  ==========================================
echo    HESABDAR  -  Salon accounting installer
echo  ==========================================
echo.

rem ---- 1) find Python 3.10+ -------------------------------------------
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3 -c "import sys; assert sys.version_info>=(3,10)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; assert sys.version_info>=(3,10)" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [1/4] Python not found. Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo  Python was installed. Close this window and run install.bat again.
  echo  If winget is not available, download Python from https://www.python.org/downloads/
  echo  and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)
echo [1/4] Python found: %PY%

rem ---- 2) virtual environment -------------------------------------------
if not exist "backend\.venv\Scripts\python.exe" (
  echo [2/4] Creating virtual environment...
  %PY% -m venv backend\.venv || goto :fail
) else (
  echo [2/4] Virtual environment exists.
)

rem ---- 3) packages --------------------------------------------------------
echo [3/4] Installing packages (first time takes a few minutes)...
backend\.venv\Scripts\python -m pip install --upgrade pip -q
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt -q || goto :fail

rem ---- 4) desktop shortcut ------------------------------------------------
echo [4/4] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Hesabdar.lnk');" ^
  "$s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0hesabdar.ico'; $s.Save()" >nul 2>&1

echo.
echo  Installation finished. Starting HESABDAR...
echo.
call "%~dp0start.bat"
exit /b 0

:fail
echo.
echo  Installation failed. Check your internet connection and run install.bat again.
pause
exit /b 1
