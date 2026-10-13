# Threat model

## What this project does and where untrusted input enters
CViche converts faculty CVs into Weill Cornell Medicine's Word CV template. A FastAPI backend
(`web_interface/backend/`) accepts CV uploads, runs a multi-stage LLM pipeline
(`src/unified_pipeline/`, models via AWS Bedrock) and returns a generated .docx. A React frontend
(`web_interface/frontend/`) drives it. It runs on AWS EKS behind WCM SAML SSO and is not on the
public internet today, but it may be exposed publicly later, so treat every network-reachable
endpoint as internet-facing.

Uploaded CVs contain personal data: names, addresses, phone numbers, email addresses, sometimes
dates of birth. Untrusted input enters through:
- Uploaded files (.docx, .doc, .pdf), single and batch (`app/api/upload.py`, `app/api/batches.py`):
  parsed with python-docx/lxml, pdfplumber, pdf2image/poppler.
- Text extracted from those files, which goes into LLM prompts (prompt injection) and whose
  LLM output flows into the generated .docx and the web UI.
- The SAML login flow (`app/saml_client.py`, `app/saml_routes.py`, `app/saml_replay.py`) and
  session cookies (`app/auth.py`, `app/session_idle.py`).
- Every HTTP and WebSocket endpoint under `app/api/`, including admin routes.
- Filenames and metadata on uploads (`Content-Disposition`, S3 keys, local paths).

## Components that matter most / least
Most: authentication/authorization and role checks (user, staff, admin), so that no user can read
another user's runs, CVs or outputs (IDOR); upload handling and file parsing; path and S3 key
construction from user-controlled values; SAML assertion validation; redirect handling
(`app/redirect_safety.py`); anything that could leak CV PII into logs, Teams/email notifications
or another user's view.
Less: offline analysis scripts in `scripts/`, the CLI driver `run_full_pipeline.py`, and
`mock-idp/` (local development only). `archive/` is dead code and out of scope.

## How to exercise it
- Backend tests: `cd web_interface/backend && python -m pytest` (in-memory SQLite; DB_* env vars
  are dummies set in the image).
- Pipeline tests: `python -m pytest src/unified_pipeline/tests/`.
- Frontend: `cd web_interface/frontend && npm test`.
LLM calls need AWS credentials and are unavailable offline. Tests mock them.

## How you rate severity
- Critical: authentication bypass, RCE, or reading/modifying another user's CVs or outputs.
- High: PII disclosure across users or into logs/notifications, privilege escalation to admin
  or staff, SAML signature/replay flaws, stored XSS, path traversal or arbitrary file write.
- Medium: SSRF, CSRF on state-changing endpoints, open redirect, a single upload that
  takes down a worker (parser DoS).
- Low: issues needing an already-authenticated admin, or missing hardening with no exploit path.
- Prompt injection matters only when it crosses a trust boundary (one user's content reaching
  another user, or causing an action beyond rewriting that user's own CV).

## Anything to leave alone
- Hallucinated or misplaced CV content in the generated document is a quality bug, not a security one.
- Dependency CVEs with no reachable code path (pip-audit runs in CI).
- Test fixtures and dummy secrets in tests and this Dockerfile.
