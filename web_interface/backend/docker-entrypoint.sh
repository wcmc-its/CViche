#!/bin/bash
set -e

# alembic.ini, the alembic/ tree and the `app` package all live here.
cd /app/web_interface/backend

echo "==> Running Alembic migrations..."
alembic upgrade head
echo "==> Migrations complete."

echo "==> Starting uvicorn..."
# --proxy-headers: trust X-Forwarded-Proto / -For / -Host from the upstream LB
# so request.url.scheme is "https" behind ALB+ACM and absolute-URL builders
# (SAML metadata, OIDC redirects) produce https:// URLs. The container is only
# reachable via the LB in EKS, so --forwarded-allow-ips=* is safe; see
# docs/PRODUCTION_TLS.md for the contract the LB must honor.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --proxy-headers \
    --forwarded-allow-ips='*' \
    "$@"
