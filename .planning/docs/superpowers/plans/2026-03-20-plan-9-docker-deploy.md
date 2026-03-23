# Plan 9: Docker Containerization & Deployment

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Containerize the CViche backend (FastAPI) and frontend (React/Vite) with Docker, provide docker-compose for local development and production, create an nginx reverse proxy config for the frontend, and produce Kubernetes manifests for EKS deployment. Ensure Alembic migrations run on container startup and all environment variables from the spec are wired through.

**Architecture:** Two-container setup: a Python backend container running FastAPI via uvicorn (port 8000), and a frontend container using a multi-stage build (Node for Vite build, nginx for static serving on port 80). nginx proxies `/api` and `/ws` requests to the backend. docker-compose orchestrates both containers plus a MariaDB database for local dev. Kubernetes manifests target EKS with ConfigMap-mounted config files, Secret-referenced credentials, and S3-backed storage.

**Tech Stack:** Docker, docker-compose, nginx, Kubernetes (Deployment, Service, ConfigMap, Secret, Ingress), Alembic, MariaDB

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` -- section 9 (Storage & EKS Deployment) and Environment Variables Summary table.

**Depends on:** Plan 1 (Auth Foundation -- database, models, Alembic setup)

---

### Task 1: Backend Dockerfile

**Files:**
- Create: `web_interface/backend/Dockerfile`

- [ ] **Step 1: Create backend Dockerfile**

Create `web_interface/backend/Dockerfile`:

```dockerfile
# ---- Backend Dockerfile ----
# Python 3.11 slim for smaller image size
FROM python:3.11-slim AS base

# Install system dependencies needed by pdf2image and pdfplumber
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for uploads and outputs (used in local storage mode)
RUN mkdir -p /app/uploads /app/outputs /app/prompt_logs

# Expose uvicorn port
EXPOSE 8000

# Health check: hit the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint script handles Alembic migrations + uvicorn startup
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
```

- [ ] **Step 2: Create backend entrypoint script**

Create `web_interface/backend/docker-entrypoint.sh`:

```bash
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
```

- [ ] **Step 3: Verify Dockerfile builds locally**

Run:
```bash
cd web_interface/backend
docker build -t cviche-backend .
```
Expected: successful build, no errors.

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/Dockerfile web_interface/backend/docker-entrypoint.sh
git commit -m "feat: add backend Dockerfile with Alembic migration entrypoint"
```

---

### Task 2: Backend .dockerignore

**Files:**
- Create: `web_interface/backend/.dockerignore`

- [ ] **Step 1: Create backend .dockerignore**

Create `web_interface/backend/.dockerignore`:

```
__pycache__
*.pyc
*.pyo
.env
.env.*
*.db
*.sqlite3
cviche.db
pipeline.db
prompt_logs/
.git
.gitignore
.DS_Store
venv/
.venv/
*.egg-info
dist/
build/
.pytest_cache/
.mypy_cache/
node_modules/
README.md
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/backend/.dockerignore
git commit -m "chore: add backend .dockerignore"
```

---

### Task 3: Frontend Dockerfile (Multi-Stage)

**Files:**
- Create: `web_interface/frontend/Dockerfile`

- [ ] **Step 1: Create frontend multi-stage Dockerfile**

Create `web_interface/frontend/Dockerfile`:

```dockerfile
# ---- Stage 1: Build ----
FROM node:20-alpine AS build

WORKDIR /app

# Copy package files first for layer caching
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# ---- Stage 2: Serve with nginx ----
FROM nginx:1.27-alpine AS production

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Expose HTTP port
EXPOSE 80

# Health check: verify nginx responds
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

- [ ] **Step 2: Verify Dockerfile syntax**

Run:
```bash
cd web_interface/frontend
docker build -t cviche-frontend .
```
Note: this will fail until nginx.conf is created in Task 4. That is expected.

- [ ] **Step 3: Commit**

```bash
git add web_interface/frontend/Dockerfile
git commit -m "feat: add frontend multi-stage Dockerfile (Node build + nginx serve)"
```

---

### Task 4: Frontend .dockerignore

**Files:**
- Create: `web_interface/frontend/.dockerignore`

- [ ] **Step 1: Create frontend .dockerignore**

Create `web_interface/frontend/.dockerignore`:

```
node_modules
dist
.DS_Store
.git
.gitignore
*.md
.vscode
.env
.env.*
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/.dockerignore
git commit -m "chore: add frontend .dockerignore"
```

---

### Task 5: nginx.conf for Frontend

**Files:**
- Create: `web_interface/frontend/nginx.conf`

- [ ] **Step 1: Create nginx.conf**

Create `web_interface/frontend/nginx.conf`:

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # Gzip compression for static assets
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript image/svg+xml;
    gzip_min_length 1000;
    gzip_vary on;

    # Proxy API requests to the backend
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Increase timeouts for long-running pipeline operations
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;

        # Pass cookies for session auth
        proxy_set_header Cookie $http_cookie;
    }

    # Proxy WebSocket connections to the backend
    location /ws/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeout (keep connection alive for pipeline duration)
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # Pass cookies for session auth on WebSocket upgrade
        proxy_set_header Cookie $http_cookie;
    }

    # Serve static files with caching
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # SPA fallback: serve index.html for all non-file routes
    # This enables React Router client-side routing
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Increase max upload size for CV files
    client_max_body_size 50M;
}
```

- [ ] **Step 2: Build frontend image to verify nginx config**

Run:
```bash
cd web_interface/frontend
docker build -t cviche-frontend .
```
Expected: successful build.

- [ ] **Step 3: Quick smoke test**

Run:
```bash
docker run -d --name cviche-frontend-test -p 8080:80 cviche-frontend
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/
docker stop cviche-frontend-test && docker rm cviche-frontend-test
```
Expected: HTTP 200.

- [ ] **Step 4: Commit**

```bash
git add web_interface/frontend/nginx.conf
git commit -m "feat: add nginx config for frontend with API/WebSocket proxy"
```

---

### Task 6: docker-compose.yml for Local Development

**Files:**
- Create: `web_interface/docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

Create `web_interface/docker-compose.yml`:

```yaml
# docker-compose.yml -- Local development
# Usage: docker compose up --build
version: "3.9"

services:
  # --- MariaDB ---
  db:
    image: mariadb:11
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: cviche_dev
      MARIADB_DATABASE: cviche
      MARIADB_CHARACTER_SET_SERVER: utf8mb4
      MARIADB_COLLATION_SERVER: utf8mb4_unicode_ci
    ports:
      - "3306:3306"
    volumes:
      - mariadb_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

  # --- Backend (FastAPI) ---
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"
    environment:
      # Database
      CVICHE_DATABASE_URL: "mysql+pymysql://root:cviche_dev@db:3306/cviche"
      # Session (random for dev -- sessions lost on restart, as expected)
      CVICHE_SESSION_SECRET: "dev-secret-change-in-production"
      # Cookies: no TLS in local dev
      CVICHE_SECURE_COOKIES: "false"
      # CORS: allow frontend dev server and nginx
      CVICHE_ALLOWED_ORIGINS: "http://localhost:3000,http://localhost:5173,http://localhost:80,http://localhost"
      # Storage: local filesystem for dev
      CVICHE_STORAGE_BACKEND: "local"
      # OpenAI API key -- read from host environment
      OPENAI_API_KEY_WORK: "${OPENAI_API_KEY_WORK}"
    volumes:
      # Hot-reload: mount source code
      - ./backend/app:/app/app:ro
      - ./backend/auth_config.yaml:/app/auth_config.yaml:ro
      - ./backend/consent_text.md:/app/consent_text.md:ro
      # Persist uploads and outputs across restarts
      - backend_uploads:/app/uploads
      - backend_outputs:/app/outputs
      - backend_prompt_logs:/app/prompt_logs
    # Override entrypoint for dev: add --reload flag to uvicorn
    command: ["--reload", "--reload-dir", "/app/app"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s

  # --- Frontend (Vite dev server for hot-reload) ---
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
      target: build
    restart: unless-stopped
    depends_on:
      backend:
        condition: service_healthy
    ports:
      - "3000:3000"
    environment:
      NODE_ENV: development
    volumes:
      # Hot-reload: mount source code (exclude node_modules)
      - ./frontend/src:/app/src:ro
      - ./frontend/public:/app/public:ro
      - ./frontend/index.html:/app/index.html:ro
      - ./frontend/vite.config.ts:/app/vite.config.ts:ro
      - ./frontend/tailwind.config.js:/app/tailwind.config.js:ro
      - ./frontend/postcss.config.js:/app/postcss.config.js:ro
      - ./frontend/tsconfig.json:/app/tsconfig.json:ro
    # In dev mode, run Vite dev server instead of nginx
    command: ["npx", "vite", "--host", "0.0.0.0", "--port", "3000"]

volumes:
  mariadb_data:
  backend_uploads:
  backend_outputs:
  backend_prompt_logs:
```

- [ ] **Step 2: Verify compose config**

Run:
```bash
cd web_interface
docker compose config
```
Expected: valid YAML, no errors.

- [ ] **Step 3: Test full stack startup**

Run:
```bash
cd web_interface
docker compose up --build -d
docker compose ps
```
Expected: all three services running and healthy.

- [ ] **Step 4: Verify backend health**

Run:
```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```
Expected: `{"status": "healthy"}`

- [ ] **Step 5: Verify frontend serves**

Run:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/
```
Expected: HTTP 200.

- [ ] **Step 6: Tear down**

Run:
```bash
cd web_interface
docker compose down
```

- [ ] **Step 7: Commit**

```bash
git add web_interface/docker-compose.yml
git commit -m "feat: add docker-compose.yml for local development with hot-reload"
```

---

### Task 7: docker-compose.prod.yml for Production

**Files:**
- Create: `web_interface/docker-compose.prod.yml`

- [ ] **Step 1: Create production compose override**

Create `web_interface/docker-compose.prod.yml`:

```yaml
# docker-compose.prod.yml -- Production settings
# Usage: docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
version: "3.9"

services:
  # --- Backend (production) ---
  backend:
    restart: always
    environment:
      # Production overrides (set via .env or deployment pipeline)
      CVICHE_DATABASE_URL: "${CVICHE_DATABASE_URL}"
      CVICHE_SESSION_SECRET: "${CVICHE_SESSION_SECRET}"
      CVICHE_SECURE_COOKIES: "true"
      CVICHE_ALLOWED_ORIGINS: "${CVICHE_ALLOWED_ORIGINS}"
      CVICHE_STORAGE_BACKEND: "s3"
      CVICHE_S3_BUCKET: "${CVICHE_S3_BUCKET}"
      CVICHE_S3_PREFIX: "${CVICHE_S3_PREFIX:-cviche}"
      OPENAI_API_KEY_WORK: "${OPENAI_API_KEY_WORK}"
    volumes: []  # No hot-reload mounts in production
    # Production: no --reload flag, add workers
    command: ["--workers", "4"]
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G
        reservations:
          cpus: "0.5"
          memory: 512M

  # --- Frontend (production: nginx serves built assets) ---
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
      target: production
    restart: always
    ports:
      - "80:80"
    volumes: []  # No hot-reload mounts in production
    command: ["nginx", "-g", "daemon off;"]
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
        reservations:
          cpus: "0.1"
          memory: 64M

  # --- DB: use external managed database in production ---
  # Remove the db service -- production uses a managed MariaDB instance
  db:
    profiles:
      - disabled
```

- [ ] **Step 2: Create .env.example for production**

Create `web_interface/.env.example`:

```bash
# CViche Production Environment Variables
# Copy to .env and fill in values

# Database connection string (managed MariaDB)
CVICHE_DATABASE_URL=mysql+pymysql://cviche_user:PASSWORD@mariadb-host:3306/cviche

# Session cookie signing key (generate with: python3 -c "import secrets; print(secrets.token_hex(32))")
CVICHE_SESSION_SECRET=

# CORS allowed origins (comma-separated, no trailing slashes)
CVICHE_ALLOWED_ORIGINS=https://cviche.med.cornell.edu

# S3 storage
CVICHE_S3_BUCKET=wcm-cviche-storage
CVICHE_S3_PREFIX=cviche

# OpenAI API key
OPENAI_API_KEY_WORK=
```

- [ ] **Step 3: Verify production compose config**

Run:
```bash
cd web_interface
docker compose -f docker-compose.yml -f docker-compose.prod.yml config
```
Expected: valid merged config with production overrides.

- [ ] **Step 4: Commit**

```bash
git add web_interface/docker-compose.prod.yml web_interface/.env.example
git commit -m "feat: add docker-compose.prod.yml and .env.example for production"
```

---

### Task 8: Health Check Endpoint Verification

**Files:**
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Enhance health check endpoint with DB connectivity**

Update the `/health` endpoint in `web_interface/backend/app/main.py` to verify the database connection, not just return a static response:

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db

@app.get("/health")
async def health(db: Session = Depends(get_db)):
    """Health check endpoint -- verifies DB connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "disconnected", "error": str(e)}
        )
```

Note: import `JSONResponse` from `fastapi.responses` if not already imported.

- [ ] **Step 2: Verify health check works locally**

Run:
```bash
cd web_interface/backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
sleep 2
curl -s http://localhost:8000/health | python3 -m json.tool
kill %1
```
Expected: `{"status": "healthy", "database": "connected"}`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/main.py
git commit -m "feat: enhance health check to verify database connectivity"
```

---

### Task 9: Alembic Configuration for Container Startup

**Files:**
- Create: `web_interface/backend/alembic.ini` (if not already present from Plan 1)
- Modify: `web_interface/backend/docker-entrypoint.sh` (already created in Task 1)

- [ ] **Step 1: Create alembic.ini with env var support**

Create `web_interface/backend/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

# Connection string is overridden at runtime by alembic/env.py
# using the CVICHE_DATABASE_URL environment variable.
# This fallback is for local dev only.
sqlalchemy.url = mysql+pymysql://root@localhost:3306/cviche

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Create alembic/env.py with dynamic database URL**

Create `web_interface/backend/alembic/env.py`:

```python
"""Alembic environment configuration."""
import os
import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add backend directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import Base
from app.models import Run, Step, Log, LLMUsage  # Import all models so Base.metadata is populated

# Alembic Config object
config = context.config

# Override sqlalchemy.url from environment variable if set
database_url = os.environ.get("CVICHE_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode -- generates SQL without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "format"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode -- connects to the database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create alembic/versions/ directory**

Run:
```bash
mkdir -p web_interface/backend/alembic/versions
touch web_interface/backend/alembic/versions/.gitkeep
```

- [ ] **Step 4: Verify Alembic runs with CVICHE_DATABASE_URL**

Run:
```bash
cd web_interface/backend
CVICHE_DATABASE_URL="mysql+pymysql://root@localhost:3306/cviche" alembic current
```
Expected: shows current migration revision (or empty if no migrations applied yet).

- [ ] **Step 5: Verify docker-entrypoint.sh runs migrations before uvicorn**

Review the `docker-entrypoint.sh` created in Task 1 Step 2. It should:
1. Run `alembic upgrade head` first
2. Then `exec uvicorn ...`

The `CVICHE_DATABASE_URL` env var is passed by docker-compose, so Alembic will pick it up via `alembic/env.py`.

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/alembic.ini web_interface/backend/alembic/
git commit -m "feat: configure Alembic with env var database URL for container startup"
```

---

### Task 10: Kubernetes Manifests Directory

**Files:**
- Create: `web_interface/k8s/namespace.yaml`
- Create: `web_interface/k8s/backend-deployment.yaml`
- Create: `web_interface/k8s/backend-service.yaml`
- Create: `web_interface/k8s/frontend-deployment.yaml`
- Create: `web_interface/k8s/frontend-service.yaml`
- Create: `web_interface/k8s/configmap.yaml`
- Create: `web_interface/k8s/secrets.yaml`
- Create: `web_interface/k8s/ingress.yaml`

- [ ] **Step 1: Create namespace manifest**

Create `web_interface/k8s/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: cviche
  labels:
    app: cviche
```

- [ ] **Step 2: Create ConfigMap for auth_config.yaml and consent_text.md**

Create `web_interface/k8s/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cviche-config
  namespace: cviche
  labels:
    app: cviche
data:
  auth_config.yaml: |
    auth:
      mode: simple

    allowed_users:
      - paa2013@med.cornell.edu

    admin_users:
      - paa2013@med.cornell.edu

    rate_limits:
      daily: 10
      monthly: 50

    consent:
      version: "1.0"

  consent_text.md: |
    # CViche Consent

    ## About This Project

    The Samuel J. Wood Library is conducting a pilot of CViche, a prototype tool
    that uses AI to convert CVs into WCM format.

    ## How Your CV Will Be Used

    Your CV will be processed using AI. The output will be reviewed by project
    staff, and feedback will be analyzed to improve the system.

    ## Sensitive Information

    You may omit personal contact details before submission.

    ## Consent

    By proceeding, you consent to participate in this pilot project.
```

- [ ] **Step 3: Create Secret references manifest**

Create `web_interface/k8s/secrets.yaml`:

```yaml
# Template -- do NOT commit actual secret values.
# Apply with: kubectl create secret generic cviche-secrets \
#   --namespace=cviche \
#   --from-literal=CVICHE_DATABASE_URL='mysql+pymysql://...' \
#   --from-literal=CVICHE_SESSION_SECRET='...' \
#   --from-literal=OPENAI_API_KEY_WORK='...'
apiVersion: v1
kind: Secret
metadata:
  name: cviche-secrets
  namespace: cviche
  labels:
    app: cviche
type: Opaque
stringData:
  CVICHE_DATABASE_URL: "REPLACE_WITH_ACTUAL_VALUE"
  CVICHE_SESSION_SECRET: "REPLACE_WITH_ACTUAL_VALUE"
  OPENAI_API_KEY_WORK: "REPLACE_WITH_ACTUAL_VALUE"
```

- [ ] **Step 4: Create backend Deployment**

Create `web_interface/k8s/backend-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cviche-backend
  namespace: cviche
  labels:
    app: cviche
    component: backend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: cviche
      component: backend
  template:
    metadata:
      labels:
        app: cviche
        component: backend
    spec:
      serviceAccountName: cviche-backend
      containers:
        - name: backend
          image: REGISTRY/cviche-backend:latest
          ports:
            - containerPort: 8000
              protocol: TCP
          env:
            # From Secrets
            - name: CVICHE_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: cviche-secrets
                  key: CVICHE_DATABASE_URL
            - name: CVICHE_SESSION_SECRET
              valueFrom:
                secretKeyRef:
                  name: cviche-secrets
                  key: CVICHE_SESSION_SECRET
            - name: OPENAI_API_KEY_WORK
              valueFrom:
                secretKeyRef:
                  name: cviche-secrets
                  key: OPENAI_API_KEY_WORK
            # From ConfigMap / static values
            - name: CVICHE_SECURE_COOKIES
              value: "true"
            - name: CVICHE_ALLOWED_ORIGINS
              value: "https://cviche.med.cornell.edu"
            - name: CVICHE_STORAGE_BACKEND
              value: "s3"
            - name: CVICHE_S3_BUCKET
              value: "wcm-cviche-storage"
            - name: CVICHE_S3_PREFIX
              value: "cviche"
          volumeMounts:
            - name: config-volume
              mountPath: /app/auth_config.yaml
              subPath: auth_config.yaml
              readOnly: true
            - name: config-volume
              mountPath: /app/consent_text.md
              subPath: consent_text.md
              readOnly: true
          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          startupProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 30  # Allow up to 150s for Alembic migrations on first deploy
      volumes:
        - name: config-volume
          configMap:
            name: cviche-config
```

- [ ] **Step 5: Create backend Service**

Create `web_interface/k8s/backend-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: cviche
  labels:
    app: cviche
    component: backend
spec:
  type: ClusterIP
  selector:
    app: cviche
    component: backend
  ports:
    - name: http
      port: 8000
      targetPort: 8000
      protocol: TCP
```

- [ ] **Step 6: Create frontend Deployment**

Create `web_interface/k8s/frontend-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cviche-frontend
  namespace: cviche
  labels:
    app: cviche
    component: frontend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: cviche
      component: frontend
  template:
    metadata:
      labels:
        app: cviche
        component: frontend
    spec:
      containers:
        - name: frontend
          image: REGISTRY/cviche-frontend:latest
          ports:
            - containerPort: 80
              protocol: TCP
          resources:
            requests:
              cpu: "50m"
              memory: "64Mi"
            limits:
              cpu: "500m"
              memory: "256Mi"
          livenessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
```

- [ ] **Step 7: Create frontend Service**

Create `web_interface/k8s/frontend-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: cviche
  labels:
    app: cviche
    component: frontend
spec:
  type: ClusterIP
  selector:
    app: cviche
    component: frontend
  ports:
    - name: http
      port: 80
      targetPort: 80
      protocol: TCP
```

- [ ] **Step 8: Create Ingress**

Create `web_interface/k8s/ingress.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cviche-ingress
  namespace: cviche
  labels:
    app: cviche
  annotations:
    # AWS ALB Ingress Controller annotations
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/certificate-arn: "REPLACE_WITH_ACM_CERT_ARN"
    alb.ingress.kubernetes.io/ssl-redirect: "443"
    # Sticky sessions for WebSocket connections
    alb.ingress.kubernetes.io/target-group-attributes: stickiness.enabled=true,stickiness.lb_cookie.duration_seconds=3600
    # Health check
    alb.ingress.kubernetes.io/healthcheck-path: /
    alb.ingress.kubernetes.io/healthcheck-interval-seconds: "30"
spec:
  rules:
    - host: cviche.med.cornell.edu
      http:
        paths:
          # All traffic goes to the frontend nginx, which proxies /api and /ws to backend
          - path: /
            pathType: Prefix
            backend:
              service:
                name: frontend
                port:
                  number: 80
```

- [ ] **Step 9: Commit**

```bash
git add web_interface/k8s/
git commit -m "feat: add Kubernetes manifests for EKS deployment"
```

---

### Task 11: Environment Variables Wiring Verification

This task verifies that all environment variables from the spec's table are correctly referenced across all configuration files.

**Reference (from spec section 9):**

| Variable | Required | Default | Used In |
|----------|----------|---------|---------|
| `CVICHE_DATABASE_URL` | Yes (prod) | `mysql+pymysql://root@localhost:3306/cviche` | `database.py`, `alembic/env.py`, `docker-compose.yml`, K8s Secret |
| `CVICHE_SESSION_SECRET` | Yes (prod) | Random (dev) | `auth.py`, `docker-compose.yml`, K8s Secret |
| `CVICHE_SECURE_COOKIES` | No | `true` | `auth.py`, `docker-compose.yml`, K8s Deployment env |
| `CVICHE_ALLOWED_ORIGINS` | No | `http://localhost:3000,http://localhost:5173` | `main.py`, `docker-compose.yml`, K8s Deployment env |
| `CVICHE_STORAGE_BACKEND` | No | `local` | storage module, `docker-compose.yml`, K8s Deployment env |
| `CVICHE_S3_BUCKET` | If s3 | -- | storage module, `docker-compose.prod.yml`, K8s Deployment env |
| `CVICHE_S3_PREFIX` | No | `cviche` | storage module, `docker-compose.prod.yml`, K8s Deployment env |
| `OPENAI_API_KEY_WORK` | Yes | -- | pipeline module, `docker-compose.yml`, K8s Secret |

- [ ] **Step 1: Verify backend reads all env vars**

Run:
```bash
cd web_interface/backend
grep -rn "CVICHE_\|OPENAI_API_KEY_WORK" app/ alembic/ docker-entrypoint.sh
```
Expected: each variable appears in the appropriate file.

- [ ] **Step 2: Verify docker-compose.yml passes all env vars**

Run:
```bash
grep -n "CVICHE_\|OPENAI_" web_interface/docker-compose.yml
```
Expected: all 8 variables listed.

- [ ] **Step 3: Verify K8s manifests reference all env vars**

Run:
```bash
grep -n "CVICHE_\|OPENAI_" web_interface/k8s/backend-deployment.yaml web_interface/k8s/secrets.yaml
```
Expected: all 8 variables present across the two files.

- [ ] **Step 4: Verify docker-compose.prod.yml overrides correctly**

Run:
```bash
grep -n "CVICHE_\|OPENAI_" web_interface/docker-compose.prod.yml
```
Expected: production overrides for `CVICHE_STORAGE_BACKEND=s3`, `CVICHE_SECURE_COOKIES=true`, S3 variables.

No commit for this task -- it is a verification step.

---

### Summary

| Task | Description | Files | Estimated Time |
|------|-------------|-------|----------------|
| 1 | Backend Dockerfile | `backend/Dockerfile`, `backend/docker-entrypoint.sh` | 10 min |
| 2 | Backend .dockerignore | `backend/.dockerignore` | 2 min |
| 3 | Frontend Dockerfile (multi-stage) | `frontend/Dockerfile` | 5 min |
| 4 | Frontend .dockerignore | `frontend/.dockerignore` | 2 min |
| 5 | nginx.conf | `frontend/nginx.conf` | 10 min |
| 6 | docker-compose.yml (dev) | `docker-compose.yml` | 15 min |
| 7 | docker-compose.prod.yml | `docker-compose.prod.yml`, `.env.example` | 10 min |
| 8 | Health check endpoint enhancement | `backend/app/main.py` | 5 min |
| 9 | Alembic container config | `backend/alembic.ini`, `backend/alembic/env.py` | 10 min |
| 10 | Kubernetes manifests | `k8s/*.yaml` (8 files) | 25 min |
| 11 | Environment variable verification | (no new files) | 5 min |

**Total: ~99 minutes (11 tasks, ~30 steps)**

### Files Created/Modified

**New files (16):**
- `web_interface/backend/Dockerfile`
- `web_interface/backend/docker-entrypoint.sh`
- `web_interface/backend/.dockerignore`
- `web_interface/backend/alembic.ini`
- `web_interface/backend/alembic/env.py`
- `web_interface/backend/alembic/versions/.gitkeep`
- `web_interface/frontend/Dockerfile`
- `web_interface/frontend/.dockerignore`
- `web_interface/frontend/nginx.conf`
- `web_interface/docker-compose.yml`
- `web_interface/docker-compose.prod.yml`
- `web_interface/.env.example`
- `web_interface/k8s/namespace.yaml`
- `web_interface/k8s/backend-deployment.yaml`
- `web_interface/k8s/backend-service.yaml`
- `web_interface/k8s/frontend-deployment.yaml`
- `web_interface/k8s/frontend-service.yaml`
- `web_interface/k8s/configmap.yaml`
- `web_interface/k8s/secrets.yaml`
- `web_interface/k8s/ingress.yaml`

**Modified files (1):**
- `web_interface/backend/app/main.py` (enhanced health check)
