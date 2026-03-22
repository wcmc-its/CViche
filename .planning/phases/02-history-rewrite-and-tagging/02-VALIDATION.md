---
phase: 2
slug: history-rewrite-and-tagging
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Shell scripts (bash verification) |
| **Config file** | None — verification is command-based |
| **Quick run command** | `du -sh .git && git ls-files \| wc -l` |
| **Full suite command** | Custom blob scan script (see Wave 0) |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `du -sh .git && git ls-files | wc -l`
- **After every plan wave:** Run full blob scan script
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | GIT-03 | automated | `git log --all --diff-filter=ACMR --name-only -- "src/logs/*.jsonl" \| wc -l` returns 0 after rewrite | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | GIT-03 | automated | Blob scan script: enumerate all objects, check for targeted extensions/paths | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | GIT-03 | automated | `du -sh .git` — parse output, assert < 50M | ❌ W0 | ⬜ pending |
| 02-01-04 | 01 | 1 | GIT-03 | automated | `git log --all --diff-filter=ACMR --name-only -- "*/auth_config.yaml" \| grep -v example` returns empty | ❌ W0 | ⬜ pending |
| 02-01-05 | 01 | 1 | VER-02 | automated | `git tag -l v1.0.0` returns non-empty | ❌ W0 | ⬜ pending |
| 02-01-06 | 01 | 1 | VER-02 | automated | `git cat-file -t v1.0.0` returns "tag" | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Blob scan verification script — enumerate all blobs in history, check none match targeted extensions (`*.pdf`, `*.docx`, `*.sqlite`, `*.db`, `*.tar.gz`, `*.key`, `*.jsonl`) or paths (`data/`, `archive/`, `node_modules/`, `uploads/`, `prompt_logs/`, `outputs/`, `src/logs/`, `key_files/`)
- [ ] `.gitignore` update for `src/logs/` — must be done before BFG runs
- [ ] `git rm src/logs/*.jsonl` — remove tracked files from HEAD before BFG

*Existing infrastructure: git commands provide all verification primitives.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Working tree intact after rewrite | GIT-03 | Functional verification of app state | Confirm all tracked source files are present and unchanged; spot-check a few Python/YAML files |
| Dry-run output review | GIT-03 | User decision gate | Review BFG report showing what would be purged; approve before actual rewrite |
| Tag approval | VER-02 | User decision gate | Review verification results; type "approved" to proceed with tagging |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
