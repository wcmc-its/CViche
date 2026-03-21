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
