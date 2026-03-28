---
phase: 18-documentation
plan: 01
subsystem: docs
tags: [readme, architecture, documentation, security, env-vars]

# Dependency graph
requires:
  - phase: 15-security-hardening
    provides: "Security measures, service layer, frontend architecture to document"
  - phase: 12-auth
    provides: "Dual-mode auth (simple + SAML) to document"
provides:
  - "Comprehensive README.md covering full system architecture"
  - ".gitignore entry for docs/HANDOFF.md"
affects: [18-02-PLAN, 18-03-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Two-tier documentation (README = entry point, TECHNICAL_README = deep reference)", "Security info split (feature-level in README, thresholds in HANDOFF)"]

key-files:
  created: []
  modified:
    - "README.md"
    - ".gitignore"

key-decisions:
  - "Used Mermaid diagram for architecture visualization (GitHub renders natively)"
  - "Grouped environment variables by category (API keys, database/storage, auth, security, frontend)"
  - "Security-sensitive env vars listed without default values per D-09"
  - "Preserved all existing good content (pipeline stages, sample CV, adapting, versioning)"

patterns-established:
  - "README sections: Architecture, Service Layer, Authentication, Security, Frontend Architecture, Environment Variables"
  - "Environment variable documentation pattern: grouped tables with non-sensitive defaults shown, security-sensitive defaults omitted"

requirements-completed: [DOC-05]

# Metrics
duration: 3min
completed: 2026-03-28
---

# Phase 18 Plan 01: README Rewrite Summary

**Comprehensive README.md covering pipeline, web architecture (3-layer Mermaid diagram), service layer, dual-mode auth, defense-in-depth security, frontend API client, and grouped CVICHE_* environment variable tables**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-28T01:56:49Z
- **Completed:** 2026-03-28T02:00:27Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Rewrote README.md from 152 lines to 318 lines covering the full current architecture (pipeline, web stack, service layer, security, auth)
- Added 6 new sections: Architecture, Service Layer, Authentication, Security, Frontend Architecture, Environment Variables
- Added Mermaid architecture diagram showing three-layer system (pipeline, backend, frontend)
- Updated .gitignore to exclude docs/HANDOFF.md before it is created (Plan 03)
- Documented both Docker (8000/3000) and local dev (5002/3001) setup paths
- Environment variable table sourced from config_service.py, auth.py, database.py, storage/factory.py
- No security threshold values exposed in committed content

## Task Commits

Each task was committed atomically:

1. **Task 1: Update .gitignore to exclude docs/HANDOFF.md** - `9e21627` (chore)
2. **Task 2: Rewrite README.md with comprehensive architecture documentation** - `5b075fd` (feat)

## Files Created/Modified
- `.gitignore` - Added docs/HANDOFF.md exclusion entry with descriptive comment
- `README.md` - Comprehensive rewrite: 152 to 318 lines, 6 new sections, Mermaid diagram, env var tables

## Decisions Made
- Used Mermaid (not ASCII art) for architecture diagram -- GitHub renders it natively, cleaner and more maintainable
- Grouped environment variables into 5 categories (API keys, database/storage, auth/sessions, security, frontend) for readability
- Security-sensitive env vars (session TTL, rate limits, upload size, CORS origins) listed without default values per D-09
- Added Getting Started section back (was in original README but restructured with config reference)
- Included VITE_API_URL in frontend env var section for completeness

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

- Initial README draft was 265 lines (below 300 minimum). Expanded Getting Started section and restructured env var table into grouped sub-tables to reach 318 lines.
- ARCHITECTURE.md and STACK.md referenced in plan's read_first did not exist in the worktree (exist only in .planning/ which is gitignored). Used the main repo source files and 18-RESEARCH.md as architecture reference instead.
- Vite config in this worktree still targets port 8000 (main repo already updated to 5002). Documented the correct port 5002 as the plan specifies.

## User Setup Required

None -- no external service configuration required.

## Next Phase Readiness
- README.md is complete and ready as the project entry point
- .gitignore prepared for Plan 03 (HANDOFF.md creation)
- Plan 02 (TECHNICAL_README.md update) can proceed independently
- Cross-references to TECHNICAL_README.md are in place, awaiting Plan 02 content

## Self-Check: PASSED

- FOUND: README.md
- FOUND: .gitignore
- FOUND: 18-01-SUMMARY.md
- FOUND: 9e21627 (Task 1 commit)
- FOUND: 5b075fd (Task 2 commit)

---
*Phase: 18-documentation*
*Completed: 2026-03-28*
