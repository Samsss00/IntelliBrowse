#!/usr/bin/env sh
set -e

# Make sure /app is on PYTHONPATH
export PYTHONPATH="${PYTHONPATH:-/app}"

# Auto-detect where main.py lives
if [ -f /app/main.py ]; then
  MODULE="main:app"
elif [ -f /app/app/main.py ]; then
  MODULE="app.main:app"
else
  echo "ERROR: main.py not found at /app/main.py or /app/app/main.py"
  echo "Contents of /app:"
  ls -la /app
  exit 1
fi

exec uvicorn "$MODULE" --host 0.0.0.0 --port 8000
