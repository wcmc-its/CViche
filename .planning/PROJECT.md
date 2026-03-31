# CViche

## What This Is

CViche is an AI-powered pipeline that converts unstructured academic CVs (.docx) into standardized WCM institutional format. It includes a 12-stage processing pipeline (segmentation, classification, field extraction, PubMed enrichment, Word output) and a web interface for uploading CVs, monitoring pipeline progress in real time, and downloading results. The web interface is secured with defense-in-depth practices (security headers, error sanitization, upload validation, CORS lockdown) and features a clean service-layer backend architecture with centralized frontend modules. End users are WCM faculty and staff; the Library administers the service.

## Core Value

Faculty can upload a CV and get back a correctly formatted WCM document without manual reformatting.

## Current Milestone: v1.5 LLM Provider Abstraction

**Goal:** Pipeline stages can run on any supported LLM provider (OpenAI, AWS Bedrock), configurable per deployment, without breaking existing OpenAI-only setups.

**Target features:**
- LLM provider abstraction layer (unified interface for all pipeline LLM calls)
- AWS Bedrock provider implementation (boto3 bedrock-runtime, Claude/Llama/Mistral)
- Per-stage model configuration (different models for different pipeline stages if desired)
- Global default + per-stage override config pattern
- OpenAI remains the default — zero changes for existing deployments
- Cost tracking across providers (existing LLMUsage table extended with provider field)

## Current State

**Latest release:** v1.4 (shipped 2026-03-28)
**In progress:** v1.5 Phase 19 complete — config system, LLM client, and cost tracking layer built

Six milestones in progress across 19 phases (38 plans):
- v1.0: Repository sanitized, documented, public on GitHub
- v1.1: Feedback form, run history table, end-user help
- v1.2: Dual-mode auth (simple + SAML), ED group authorization, mock IdP testing
- v1.3: Security hardening (7 fixes), backend service layer (6 modules), frontend architecture (28 typed API functions, 17 shared types)
- v1.4: Environment fixes, run history UX, comprehensive documentation (README rewrite, TECHNICAL_README v16.0, developer handoff)

**Tech stack:** React + Vite + Tailwind CSS (frontend), FastAPI + MariaDB (backend), 132+ passing tests

## Requirements

### Validated

- GIT-01 through GIT-04: Repository sanitized, history clean, .gitignore comprehensive -- v1.0
- DOC-01 through DOC-04: README, LICENSE, CHANGELOG, requirements.txt -- v1.0
- VER-01 through VER-03: Semantic versioning, v1.0.0 tag, __version__ variable -- v1.0
- TEST-01: Synthetic sample CV for pipeline testing -- v1.0
- FB-01 through FB-03: Feedback form -- v1.1
- RH-01, RH-02: Run history table redesign -- v1.1
- HELP-01, HELP-02: End-user help page -- v1.1
- MODE-01, MODE-02: Dual-mode auth -- v1.2
- SAML-01 through SAML-04: SAML SSO -- v1.2
- ED-01 through ED-03: ED group authorization -- v1.2
- TEST-01, DOC-01, SKILL-01: Auth testing, docs, skill -- v1.2
- SEC-01 through SEC-03: Critical security fixes (path traversal, SAML sig, session secret) -- v1.3
- SEC-04 through SEC-07: Security hardening (upload validation, error sanitization, headers, CORS) -- v1.3
- ARCH-01 through ARCH-06: Backend service layer (thin routes, shared services, O(1) queries) -- v1.3
- FE-01 through FE-04: Frontend architecture (centralized API client, shared types, env config, utilities) -- v1.3

- ENV-01, ENV-02: CORS and Vite proxy port fixes -- v1.4
- UX-01 through UX-03: Run history UX (feedback indicator, relative dates, column width) -- v1.4
- DOC-05: README architecture update and documentation refresh -- v1.4
- DOC-06: Developer handoff document (local-only) -- v1.4

- CFG-01, CFG-02, CFG-03: YAML config system with per-stage overrides and env var integration -- v1.5 (Phase 19)
- LLM-01, LLM-02, LLM-04: Centralized LLM client with retries, cost tracking, provider column -- v1.5 (Phase 19)

### Active

See REQUIREMENTS.md for v1.5 requirements (LLM-03 and remaining items).

### Out of Scope

- Pipeline architecture refactoring -- separate effort, large scope
- Replacing print() with structured logging -- quality improvement (3,243 print calls)
- Docker/EKS deployment -- production ops track
- Admin dashboard improvements -- admin UX is a separate milestone
- Actually registering CViche as SAML SP with WCM IdP -- pending approval
- Going live with SSO -- pending approval

## Context

- v1.0-v1.4 shipped: 18 phases, 35 plans; v1.5 Phase 19 complete (3 plans)
- Web interface is production-ready: upload, real-time pipeline viewer, download, feedback, help, dual-mode auth, security hardening
- Backend has clean service layer: 7 service modules, centralized config with CVICHE_* env overrides, structured error responses
- Frontend has single source of truth: 28 typed API functions, 17 shared interfaces, environment-derived URLs, shared formatting utilities
- Test suite: 159+ tests passing (security: 39, service layer: 26, auth: 72+, LLM abstraction: 27)
- SAML code is complete and config-gated; simple email auth remains default until WCM approval

## Constraints

- **End-user audience**: All UI must be approachable for non-technical faculty
- **Tech stack**: React + Vite + Tailwind CSS (frontend), FastAPI + MariaDB (backend)
- **SAML reference**: Janus-Auth-Manager at ~/Dropbox/GitHub/Janus-Auth-Manager/
- **No live SSO deployment**: Auth changes config-gated; simple mode default until approval

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Apache 2.0 license | Patent grant for institutional users | ✓ Good |
| Rewrite git history | PII in history as bad as in working tree | ✓ Good |
| .planning/ excluded from public repo | Internal infrastructure, not for public | ✓ Good |
| Semantic versioning | Architecture=major, models=minor, prompts=patch | ✓ Good |
| Synthetic sample CV | Real CVs contain PII | ✓ Good |
| Feedback form matches existing API | Backend is solid, no reason to change | ✓ Good |
| Dual-mode auth (simple + SAML) | Support WCM SSO and external adopters | ✓ Good (code ready) |
| pysaml2 for SAML SP | Same library as Janus, proven at WCM | ✓ Good (code ready) |
| ED group check via ldap3 | Same library as Janus, proven at WCM | ✓ Good (code ready) |
| Build ready but don't deploy SSO | CViche not yet approved for SAML | ✓ Good (config-gated) |
| login.weill.cornell.edu as discovery | Multi-institution federated login | -- Pending approval |
| Module-level RuntimeError for session secret | Fail fast at import, not at first request | ✓ Good |
| SecurityHeadersMiddleware exception handling | Starlette 0.52+ re-raises through call_next | ✓ Good |
| Service layer with errors.py factories | Consistent error format, testable services | ✓ Good |
| Security-opaque error messages in _resolve_safe_path | Don't leak internal paths to attackers | ✓ Good |
| StepSidebar retains internal StatusIcon | Different size/icons than shared component | ✓ Good (intentional) |
| VITE_API_URL defaults to empty for Vite proxy | Dev works without .env, prod uses explicit URL | ✓ Good |

---
*Last updated: 2026-03-29 after v1.4 archived — v1.5 LLM Provider Abstraction started*
