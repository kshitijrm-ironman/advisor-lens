@echo off
setlocal
REM Advisor Lens launcher: starts the server hidden, opens the browser, then this window closes.
cd /d "%~dp0"
title Advisor Lens

REM venv lives OUTSIDE the project folder: venvs hard-code absolute paths, so a synced copy breaks on another machine
set "VENV=%LOCALAPPDATA%\advisor-lens-venv"
set "PORT=8502"
set "LOG=%LOCALAPPDATA%\advisor-lens-venv\advisor-lens.log"
set "URL=http://localhost:8502"

REM Already running? Just open it and leave (no second server).
powershell -NoProfile -Command "try{Invoke-WebRequest '%URL%/_stcore/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0}catch{exit 1}"
if not errorlevel 1 (
  start "" "%URL%"
  exit /b 0
)

where python >nul 2>&1
if errorlevel 1 (
  echo [x] Python not found on PATH.
  pause
  exit /b 1
)

REM One-time: isolated virtual environment
if not exist "%VENV%\Scripts\python.exe" (
  echo [*] First run: creating virtual environment...
  python -m venv "%VENV%"
  if errorlevel 1 ( echo [x] venv creation failed & pause & exit /b 1 )
  del /q "%VENV%\.deps_ok" 2>nul
)

REM Install dependencies only once (delete %VENV%\.deps_ok to force reinstall)
if not exist "%VENV%\.deps_ok" (
  echo [*] Installing dependencies...
  "%VENV%\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
  if errorlevel 1 ( echo [x] pip install failed & pause & exit /b 1 )
  echo ok> "%VENV%\.deps_ok"
)

echo [*] Starting Advisor Lens in the background...
wscript //nologo "%~dp0run_hidden.vbs"

REM Wait up to ~40 s for Streamlit's health endpoint before opening the browser
powershell -NoProfile -Command "for($i=0;$i -lt 80;$i++){try{Invoke-WebRequest '%URL%/_stcore/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0}catch{Start-Sleep -Milliseconds 500}}; exit 1"
if errorlevel 1 (
  echo [x] Advisor Lens did not start. Last log lines:
  powershell -NoProfile -Command "Get-Content -Tail 30 '%LOG%'"
  pause
  exit /b 1
)

start "" "%URL%"
exit /b 0
