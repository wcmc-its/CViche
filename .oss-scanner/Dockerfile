# Build image for Anthropic's OSS Scanner (https://github.com/anthropics/oss-scanner).
# The scanner builds this with the repo root as context, then audits it offline,
# so every dependency the backend, pipeline and frontend tests need is fetched here.
# Mirrors web_interface/backend/Dockerfile and the ci.yml test jobs.
FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    DB_HOST=localhost \
    DB_PORT=3306 \
    DB_NAME=test \
    DB_USER=test \
    CVICHE_SESSION_SECRET=test-secret-not-for-production

# poppler-utils (PDF input), libmagic1 (upload sniffing), xmlsec1 (SAML),
# nodejs/npm (frontend), git (the agent's own diffing).
RUN apt-get update && apt-get install -y --no-install-recommends \
      poppler-utils libmagic1 xmlsec1 nodejs npm git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

RUN pip install -r web_interface/backend/requirements-dev.txt -r requirements.txt
RUN cd web_interface/frontend && npm ci

# Cheap smoke run so the image is known to work; failures do not abort the build.
RUN cd web_interface/backend && (python -m pytest -q -p no:cacheprovider \
      || echo "WARNING: backend tests reported failures")
