#!/bin/bash
# SessionStart hook for Claude Code cloud sessions: install what CI installs
# (.github/workflows/ci.yml) so the gates and both test suites run as-is.
# Local sessions are left alone. Idempotent: every step checks first.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
# Outside the repo: check_function_size.py walks the tree and only skips
# directories literally named venv/.venv.
VENV="$HOME/.venvs/cviche"
# Same pins as ci.yml: Python 3.14 (the build image) and ruff 0.16.6 (a
# version bump changes check_standards.py's counts).
PYTHON_VERSION="3.14"
RUFF_VERSION="0.16.6"
TIKTOKEN_CACHE="$HOME/.cache/cviche-tiktoken"

log() { echo "[session-start] $*" >&2; }

# System packages: xmlsec1 for the SAML tests, poppler-utils for PDF intake.
missing=()
command -v xmlsec1 >/dev/null 2>&1 || missing+=(xmlsec1)
command -v pdftotext >/dev/null 2>&1 || missing+=(poppler-utils)
if [ "${#missing[@]}" -gt 0 ]; then
  log "apt-get install ${missing[*]}"
  SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq --no-install-recommends "${missing[@]}" >/dev/null
fi

# uv provides Python 3.14 (the image ships an older python3) and fast installs.
if ! command -v uv >/dev/null 2>&1; then
  log "installing uv"
  python3 -m pip install --quiet --user uv
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import sys; sys.exit(sys.version_info[:2] != (3, 14))"; then
  log "creating $VENV (Python $PYTHON_VERSION)"
  uv python install --no-bin "$PYTHON_VERSION"
  rm -rf "$VENV"
  uv venv --quiet --python "$PYTHON_VERSION" "$VENV"
fi

log "installing Python requirements"
uv pip install --quiet --python "$VENV/bin/python" \
  -r "$REPO/web_interface/backend/requirements-dev.txt" \
  -r "$REPO/requirements.txt" \
  mypy "ruff==$RUFF_VERSION"

# Frontend: what the frontend-typecheck job needs (tsc, build, vitest).
if command -v npm >/dev/null 2>&1 && [ -f "$REPO/web_interface/frontend/package-lock.json" ]; then
  log "npm install (web_interface/frontend)"
  (cd "$REPO/web_interface/frontend" && npm_config_update_notifier=false \
    npm install --no-audit --no-fund --loglevel=error)
fi

# tiktoken fetches its o200k_base file on first use, at import time in
# segmentation/chunked_chat_hierarchy_extractor.py. Fetch it now into a cache
# that outlives the session. If the environment's network policy blocks
# openaipublic.blob.core.windows.net, say so instead of failing the hook:
# every test that imports stage 1 will fail until that host is allowed.
mkdir -p "$TIKTOKEN_CACHE"
if ! TIKTOKEN_CACHE_DIR="$TIKTOKEN_CACHE" "$VENV/bin/python" -c \
    "import tiktoken; tiktoken.get_encoding('o200k_base')" >/dev/null 2>&1; then
  log "WARNING: could not download tiktoken's o200k_base encoding."
  log "Allow openaipublic.blob.core.windows.net in this environment's network settings."
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
    echo "export TIKTOKEN_CACHE_DIR=\"$TIKTOKEN_CACHE\""
    # The backend-tests job's env from ci.yml: the DB factory refuses to
    # import without them. Test values, as in ci.yml; the suite runs on SQLite.
    echo "export DB_HOST=\"\${DB_HOST:-localhost}\" DB_PORT=\"\${DB_PORT:-3306}\""
    echo "export DB_NAME=\"\${DB_NAME:-test}\" DB_USER=\"\${DB_USER:-test}\""
  } >> "$CLAUDE_ENV_FILE"
fi

log "done: $("$VENV/bin/python" --version), ruff $("$VENV/bin/ruff" --version | cut -d' ' -f2)"
