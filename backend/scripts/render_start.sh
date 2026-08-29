#!/bin/sh
set -eu

echo "===== RUNNING DATABASE MIGRATIONS ====="
alembic upgrade head

echo "===== STARTING 9DBRAIN API ====="
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --no-access-log
