---
phase: 13-security-hardening
plan: 02
subsystem: security
tags: [fastapi, upload-validation, magic-bytes, file-size-limit, filename-sanitization, security]

# Dependency graph
requires:
  - phase: 13-01
    provides: "SecurityHeadersMiddleware, global exception handler, [SECURITY] logging pattern"
  - phase: 12-critical-security-fixes
    provides: "CSRFMiddleware, _resolve_safe_path, [SECURITY] log prefix"
provides:
  - "Magic bytes validation for PDF and DOCX uploads (_validate_pdf_magic, _validate_docx_magic)"
  - "50 MB file size limit on upload and estimate endpoints"
  - "Randomized filename storage: {run_id}.{ext} instead of {run_id}_{user_filename}"
  - "8 SEC-04 regression tests in TestUploadValidation"
affects: [pipeline-file-paths, frontend-upload-errors]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Magic bytes validation: check content bytes before saving to disk"
    - "Filename randomization: only run_id and extension on filesystem, original name in DB only"
    - "Validation-before-ID-generation: reject bad uploads before allocating run IDs"

key-files:
  created: []
  modified:
    - "web_interface/backend/app/api/upload.py"
    - "web_interface/backend/app/api/runs.py"
    - "web_interface/backend/tests/test_security.py"

key-decisions:
  - "Validate content before generating run_id so rejected uploads don't waste IDs"
  - "Use zipfile.ZipFile + namelist() check for DOCX validation (not just ZIP magic bytes) to distinguish from other ZIP-based formats"
  - "Apply same magic bytes + size validation to /estimate endpoint (file is parsed even though not saved)"

patterns-established:
  - "Upload validation order: read content -> check size -> validate magic bytes -> generate ID -> save"
  - "File path pattern: UPLOAD_DIR / f\"{run_id}.{ext}\" across upload.py and runs.py"

requirements-completed: [SEC-04]

# Metrics
duration: 3min
completed: 2026-03-26
---

# Phase 13 Plan 02: Upload Validation with Magic Bytes, Size Limit, and Filename Randomization Summary

**Magic bytes validation (PDF header, DOCX ZIP+word/document.xml), 50 MB size limit, and randomized filename storage for SEC-04 upload hardening**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-26T19:46:09Z
- **Completed:** 2026-03-26T19:48:57Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Upload endpoint validates file content matches claimed extension via magic bytes (PDF: %PDF- header, DOCX: PK ZIP header + word/document.xml)
- Files exceeding 50 MB rejected with descriptive error before any disk write
- Uploaded files stored as {run_id}.{ext} -- no user-provided filename touches the filesystem
- Estimate endpoint also validates magic bytes (same spoofed-file risk applies to parser)
- Rejected spoofed uploads logged at WARNING with [SECURITY] prefix including user email
- Pipeline start_run and restart_run updated to use new {run_id}.{file_type} filename pattern
- 8 new regression tests covering all SEC-04 behaviors (39 total security tests, 106 total tests)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add magic bytes validation, size limit, and filename randomization** - `e65ea93` (feat)
2. **Task 2: Add SEC-04 regression tests** - `ad61607` (test)

## Files Created/Modified
- `web_interface/backend/app/api/upload.py` - Added _validate_pdf_magic(), _validate_docx_magic(), MAX_UPLOAD_SIZE, size check, magic bytes validation on /upload and /estimate, randomized filename
- `web_interface/backend/app/api/runs.py` - Updated start_run (line 157) and restart_run (lines 243, 255) file path patterns to {run_id}.{file_type}
- `web_interface/backend/tests/test_security.py` - 8 new tests in TestUploadValidation: spoofed docx/pdf rejected, valid pdf accepted, oversized rejected, randomized filename, security logging, estimate validation, random bytes rejected

## Decisions Made
- Validate content before generating run_id so rejected uploads don't waste IDs (moved generate_run_id() after all validation)
- DOCX validation checks both ZIP magic bytes AND presence of word/document.xml in archive to distinguish from other ZIP formats (e.g., .xlsx, .pptx)
- Applied same validation to /estimate endpoint since file content is still parsed by pypdf/python-docx (malicious files could crash parsers)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All SEC-01 through SEC-07 requirements now complete across Phases 12 and 13
- 39 security regression tests covering all 7 security requirements
- 106 total tests passing (5 skipped for optional dependencies)
- Phase 13 security hardening complete; ready for Phase 14

## Self-Check: PASSED

- All 3 modified files exist on disk
- All 2 task commits verified in git log (e65ea93, ad61607)
- 39/39 security tests pass, 106/106 total tests pass (5 skipped)

---
*Phase: 13-security-hardening*
*Completed: 2026-03-26*
