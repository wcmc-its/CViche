#!/bin/bash
set -e

# alembic.ini, the alembic/ tree and the `app` package all live here.
cd /app/web_interface/backend

echo "==> Running Alembic migrations..."
alembic upgrade head
echo "==> Migrations complete."

echo "==> Starting uvicorn..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    "$@"
