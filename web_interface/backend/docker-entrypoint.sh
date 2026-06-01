#!/bin/bash
set -e

# alembic.ini, the alembic/ tree and the `app` package all live here.
cd /app/web_interface/backend

# `migrate` mode: run Alembic migrations and exit. Use this as the command
# for a one-shot init container / pre-deploy task in production so N
# replicas don't race the same migration against the shared database
# (see issue #5). Example:
#
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#       run --rm backend migrate
#
# or, on k8s, as an initContainer that shares the same image as the
# main container.
if [ "${1:-}" = "migrate" ]; then
    echo "==> Running Alembic migrations (one-shot)..."

     # ◄ THE IAM FIX: Dynamically intercept and swap the identity context 
    # to your high-privilege migration identity just for the scope of this one-shot run.
    export DB_USER="cviche_migration_user"
    
    exec alembic upgrade head
fi

# Default mode: optionally run migrations, then start uvicorn.
# - Single-container dev / CI: leave CVICHE_RUN_MIGRATIONS unset (defaults
#   to "1") so the entrypoint keeps schema in sync on every boot.
# - Production with >1 replica: run a separate `migrate` step first, then
#   set CVICHE_RUN_MIGRATIONS=0 here so the per-replica race goes away.
if [ "${CVICHE_RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "==> Running Alembic migrations..."
    alembic upgrade head
    echo "==> Migrations complete."
else
    echo "==> Skipping Alembic migrations (CVICHE_RUN_MIGRATIONS=0)."
fi

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
