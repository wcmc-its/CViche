# ---- Stage 1: Dependencies ----
FROM python:3.11-slim AS deps

WORKDIR /app

# Install system dependencies needed by pdf2image, pdfplumber, and xmlsec1 (SAML)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
    && find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true

# ---- Stage 2: Application ----
FROM deps AS app

WORKDIR /app

# Copy application code (tests, docs, etc. excluded via .dockerignore)
COPY . .

# Copy entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create directories for uploads and outputs (used in local storage mode)
RUN mkdir -p /app/uploads /app/outputs /app/prompt_logs

# Expose uvicorn port
EXPOSE 8000

# Health check: hit the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
