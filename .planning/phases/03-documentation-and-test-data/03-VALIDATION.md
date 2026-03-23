---
phase: 3
slug: documentation-and-test-data
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Shell commands (file existence + content checks) |
| **Config file** | none — no test framework needed for this phase |
| **Quick run command** | `test -f README.md && test -f LICENSE && test -f CHANGELOG.md` |
| **Full suite command** | `test -f README.md && grep -q "Setup" README.md && test -f LICENSE && grep -q "Apache License" LICENSE && test -f CHANGELOG.md && grep -q "1.0.0" CHANGELOG.md && python3 -c "import sys; sys.path.insert(0,'src'); from unified_pipeline import __version__; assert __version__ == '1.0.0'"` |
| **Estimated runtime** | ~2 seconds |

---

## Sampling Rate

- **After every task commit:** Run `test -f README.md && test -f LICENSE && test -f CHANGELOG.md`
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 2 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | DOC-01 | smoke | `test -f README.md && grep -q "Setup" README.md && grep -q "Pipeline" README.md` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | DOC-02 | smoke | `test -f LICENSE && grep -q "Apache License" LICENSE` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | DOC-03 | smoke | `test -f CHANGELOG.md && grep -q "1.0.0" CHANGELOG.md` | ❌ W0 | ⬜ pending |
| 03-01-04 | 01 | 1 | VER-01 | smoke | `grep -iq "versioning" README.md` | ❌ W0 | ⬜ pending |
| 03-01-05 | 01 | 1 | VER-03 | unit | `python3 -c "import sys; sys.path.insert(0,'src'); from unified_pipeline import __version__; assert __version__ == '1.0.0'"` | ✅ (empty) | ⬜ pending |
| 03-02-01 | 02 | 1 | TEST-01 | integration | `python3 run_full_pipeline.py sample_vasquez_cv --stage 1a` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `README.md` — created with required sections (DOC-01, VER-01)
- [ ] `LICENSE` — Apache 2.0 full text (DOC-02)
- [ ] `CHANGELOG.md` — Keep a Changelog format with v1.0.0 entry (DOC-03)
- [ ] `src/unified_pipeline/__init__.py` — `__version__ = '1.0.0'` added (VER-03)
- [ ] `data/sample_cvs/word/sample_vasquez_cv.docx` — synthetic CV created (TEST-01)
- [ ] `.gitignore` — negation rule added for sample CV

*Existing infrastructure covers test execution — shell commands and python one-liners suffice.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| README instructions work end-to-end | DOC-01 | Requires following multi-step human workflow | Follow README steps: clone, install deps, set OpenAI key, run CLI, check output |
| Synthetic CV processes through full pipeline | TEST-01 | Requires OpenAI API key for stages 2-5 | Run `python3 run_full_pipeline.py sample_vasquez_cv` and verify all 12 stages complete |
| Screenshots render on GitHub | DOC-01 | Requires visual inspection of rendered markdown | Push to GitHub and verify images display in README |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 2s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
