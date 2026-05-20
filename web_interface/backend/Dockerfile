# CViche backend image.
#
# Build context MUST be the repository root, not web_interface/backend/ --
# the pipeline package (src/unified_pipeline/) and the WCM Word template
# (key_files/) live at the repo root, and the orchestrator and pipeline
# resolve them through repo-root-relative paths. The image mirrors the repo
# directory layout so those paths resolve identically inside the container.
#
#   docker build -f web_interface/backend/Dockerfile -t cviche-backend .
#   docker compose build backend          # (compose sets the context)

# ---- Stage 1: Dependencies ----
FROM python:3.11-slim AS deps

WORKDIR /app

# System dependencies:
#   poppler-utils  pdf2image / pdfplumber (PDF CV input)
#   libmagic1      upload content-type sniffing
#   xmlsec1        pysaml2 SAML assertion signing/verification
#   curl           container HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libmagic1 \
    xmlsec1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install the web-backend deps AND the pipeline deps. The pipeline in src/
# carries its own requirements file (see the note in the repo-root
# requirements.txt). Copied under distinct names so this layer caches
# unless one of the two files changes.
COPY web_interface/backend/requirements.txt /tmp/web-requirements.txt
COPY requirements.txt /tmp/pipeline-requirements.txt
RUN pip install --no-cache-dir --no-compile \
        -r /tmp/web-requirements.txt \
        -r /tmp/pipeline-requirements.txt \
    && find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true

# ---- Stage 2: Application ----
FROM deps AS app

WORKDIR /app

# Mirror the repo layout. Repo-root-relative paths then resolve as:
#   orchestrator PARENT_DIR  -> /app
#   config.py PROJECT_ROOT   -> /app
#   stage 6 WCM template     -> /app/key_files/wcm_cv_template_...docx
COPY web_interface/backend/ ./web_interface/backend/
COPY src/ ./src/
COPY key_files/ ./key_files/
COPY config.yaml ./config.yaml

# Entrypoint + writable runtime directories (pipeline intermediates, uploads,
# web-tracking outputs). The pipeline and config.py also mkdir these on
# demand; pre-creating them keeps startup quiet.
RUN chmod +x ./web_interface/backend/docker-entrypoint.sh \
    && mkdir -p ./src/unified_pipeline/outputs \
                ./web_interface/outputs \
                ./web_interface/uploads \
                ./data/sample_cvs/word \
                ./web_interface/backend/prompt_logs

# alembic and uvicorn both run from the backend directory.
WORKDIR /app/web_interface/backend

EXPOSE 8000

# Health check: hit the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/web_interface/backend/docker-entrypoint.sh"]
