#!/usr/bin/env bash
# ============================================================
# Hospital Data Collection Agent - setup & run (Linux / macOS)
# ============================================================
# Usage:
#   chmod +x run.sh
#   ./run.sh
#
# What this does:
#   1. Creates a Python virtual environment in ./venv (first run only)
#   2. Installs dependencies from requirements.txt
#   3. Loads .env if present
#   4. Starts the Flask app on http://127.0.0.1:5000
# ============================================================

set -e

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "[setup] Creating virtual environment in ./venv ..."
  python3 -m venv venv
fi

echo "[setup] Activating virtual environment ..."
source venv/bin/activate

echo "[setup] Installing dependencies (pip install -r requirements.txt) ..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

if [ -f ".env" ]; then
  echo "[setup] Loading environment variables from .env ..."
  set -a
  source .env
  set +a
else
  echo "[setup] No .env found - running with default MOCK data source."
  echo "[setup] Copy .env.example to .env to configure real data sources."
fi

echo "[run] Starting Flask app at http://${HOST:-127.0.0.1}:${PORT:-5000} ..."
python -m backend.app
