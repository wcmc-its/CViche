#!/bin/bash
set -e

echo "==> Running Alembic migrations..."
cd /app
alembic upgrade head
echo "==> Migrations complete."

echo "==> Starting uvicorn..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    "$@"
