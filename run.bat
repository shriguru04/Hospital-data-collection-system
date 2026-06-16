@echo off
REM ============================================================
REM Hospital Data Collection Agent - setup & run (Windows)
REM ============================================================
REM Usage:
REM   run.bat
REM
REM What this does:
REM   1. Creates a Python virtual environment in .\venv (first run only)
REM   2. Installs dependencies from requirements.txt
REM   3. Loads .env if present
REM   4. Starts the Flask app on http://127.0.0.1:5000
REM ============================================================

cd /d "%~dp0"

if not exist venv (
  echo [setup] Creating virtual environment in .\venv ...
  python -m venv venv
)

echo [setup] Activating virtual environment ...
call venv\Scripts\activate.bat

echo [setup] Installing dependencies (pip install -r requirements.txt) ...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

if exist .env (
  echo [setup] Loading environment variables from .env ...
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
  )
) else (
  echo [setup] No .env found - running with default MOCK data source.
  echo [setup] Copy .env.example to .env to configure real data sources.
)

echo [run] Starting Flask app at http://127.0.0.1:5000 ...
python -m backend.app
