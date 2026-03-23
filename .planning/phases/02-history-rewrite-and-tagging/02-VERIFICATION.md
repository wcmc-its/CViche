---
phase: 02-history-rewrite-and-tagging
verified: 2026-03-23T01:44:40Z
status: gaps_found
score: 2/3 must-haves verified
re_verification: false
gaps:
  - truth: "Running BFG or git filter-repo confirms all targeted file types are purged from every commit in history"
    status: failed
    reason: "src/unified_pipeline/core/.outputs/ directory survives in 14 historical commits. The git-filter-repo purge targeted 'outputs/' (without leading dot) but not '.outputs/' (hidden directory). These blobs are reachable from current history. The file 2006_Bush_mapped.json contains a real faculty surname (PII) in its document_uid field."
    artifacts:
      - path: "src/unified_pipeline/core/.outputs/phase2_20cvs/2006_Bush_mapped.json"
        issue: "Survives in 14 historical commits; contains real surname 'Bush' in document_uid; was untracked from HEAD in Phase 1 but not purged from history because git-filter-repo was told to remove 'outputs/' not '.outputs/'"
      - path: "src/unified_pipeline/core/.outputs/phase2_20cvs/PHASE2_20CVS_PROCESSING_REPORT.md"
        issue: "Survives in same 14 historical commits; no direct PII but named after real CV batch (phase2_20cvs)"
    missing:
      - "Re-run git-filter-repo on a new mirror to remove all paths matching '.outputs/' across all commits"
      - "Re-run gc --prune=now --aggressive on the cleaned mirror"
      - "Replace .git with the re-cleaned mirror"
      - "Re-apply the v1.0.0 tag to the new HEAD (tag will need to move because commit hashes change)"
  - truth: "A git tag v1.0.0 exists on the final clean commit"
    status: partial
    reason: "The v1.0.0 tag is a correctly-formed annotated tag pointing to commit 66cdbc8. However, HEAD is at b9a754a (one commit ahead -- the 02-03-SUMMARY documentation commit). The tag was applied before the final documentation commit was added. The tag does NOT point to HEAD."
    artifacts:
      - path: ".git/refs/tags/v1.0.0"
        issue: "Tag points to 66cdbc8 (docs(02-02) commit) not b9a754a (HEAD, docs(02-03) commit). One clean planning commit was added after tagging."
    missing:
      - "Move the v1.0.0 tag to HEAD after the .outputs/ purge is complete: git tag -d v1.0.0 && git tag -a v1.0.0 -m '...'"
      - "Note: tag will need to move regardless since history rewrite changes all commit hashes"
---

# Phase 2: History Rewrite and Tagging Verification Report

**Phase Goal:** The git history contains zero PII and the clean state is tagged as the v1.0.0 release point
**Verified:** 2026-03-23T01:44:40Z
**Status:** gaps_found
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | BFG/filter-repo confirms all targeted file types purged from every commit | FAILED | `.outputs/` directory (containing PII) survives in 14 historical commits; git-filter-repo purged `outputs/` but not `.outputs/` |
| 2 | .git directory is under 50 MB after repacking | VERIFIED | `du -sh .git` returns `3.7M` -- well under the 50 MB threshold |
| 3 | A `git tag v1.0.0` exists on the final clean commit | PARTIAL | Tag exists and is correctly annotated, but points to `66cdbc8` not current HEAD `b9a754a` |

**Score:** 1/3 truths fully verified (1 partial, 1 failed)

---

## Required Artifacts

### 02-01-PLAN must_haves

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `.gitignore` | Coverage for `src/logs/` and `*.jsonl` | VERIFIED | Lines 69-70 contain `*.jsonl` and `src/logs/` |
| `~/Dropbox/Projects/CViche - Planning/repo-backup-pre-rewrite.git` | Complete original history backup | VERIFIED | HEAD file present; backup created 2026-03-22 |

### 02-02-PLAN must_haves

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `/tmp/cviche-clean.git` | Cleaned bare mirror (interim) | N/A | Correctly cleaned up; temporary artifact |
| `.git` | Replaced with cleaned history | VERIFIED | 3.7 MB (was 275 MB); 25 commits; working tree intact |

### 02-03-PLAN must_haves

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `.git/refs/tags/v1.0.0` | Annotated v1.0.0 tag on clean HEAD | PARTIAL | Tag is annotated (`git cat-file -t` returns `tag`); tag message correct; but points to `66cdbc8` not HEAD `b9a754a` |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `git-filter-repo` purge of `outputs/` | History free of `.outputs/` | Folder name match | NOT WIRED | git-filter-repo matched `outputs` (no dot) but `.outputs/` is a hidden directory with leading dot; BFG `--delete-folders` docs confirm folder name matching is case/dot-sensitive |
| blob scan verification | `git tag -a v1.0.0` | human approval gate | WIRED | 02-03-SUMMARY confirms user typed "approved" at checkpoint |
| `v1.0.0` tag | HEAD commit | should point to same hash | PARTIAL | Tag on `66cdbc8`; HEAD at `b9a754a`; tag preceded final documentation commit |

---

## Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| GIT-03 | 02-01-PLAN, 02-02-PLAN, 02-03-PLAN | Git history rewritten to purge PII | BLOCKED | `.outputs/` directory with faculty surname survives in 14 reachable historical commits; requirement cannot be marked satisfied while PII blobs remain reachable |
| VER-02 | 02-03-PLAN | Git tag v1.0.0 applied to the clean public release commit | PARTIAL | Tag exists and is annotated; tag message is correct; but does not point to HEAD -- one planning commit was added after the tag was applied |

**Orphaned requirements:** None. Both GIT-03 and VER-02 are claimed by the plans above. No additional Phase 2 requirements are listed in REQUIREMENTS.md beyond these two.

---

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| History (14 commits) | `.outputs/phase2_20cvs/2006_Bush_mapped.json` survives in git history | BLOCKER | Real faculty surname in `document_uid` field; PII survives in reachable git objects; fails GIT-03 |
| `.git/refs/tags/v1.0.0` | Tag not on HEAD | WARNING | Tag points 1 commit behind HEAD; v1.0.0 does not include the 02-03 documentation commit; needs to move after history re-purge |

---

## Detailed Findings

### Finding 1: `.outputs/` Directory Not Purged from History

The git-filter-repo execution in Plan 02 targeted the folder name `outputs` (without leading dot). The repository contained a hidden directory `src/unified_pipeline/core/.outputs/` (with a leading dot), which is a distinct path in git's tree. The filter-repo `--path` matching did not cover this path.

Direct evidence:

```
git rev-list --all | xargs ls-tree -r | grep '\.outputs/'
```

Returns 14 distinct commits, from `d0c94ac` (Initial commit) through `628756e` (Phase 1 docs commit), all containing:
- `src/unified_pipeline/core/.outputs/phase2_20cvs/2006_Bush_mapped.json` -- a 408-mapping JSON file where `document_uid` is `2006_Bush`, a real faculty member's surname
- `src/unified_pipeline/core/.outputs/phase2_20cvs/PHASE2_20CVS_PROCESSING_REPORT.md` -- a processing report for the 20-CV batch

Neither file is tracked in HEAD or at the v1.0.0 commit (they were removed from tracking during Phase 1 fix commit `8df2da2`). However, they remain reachable as git blob objects in historical commits, which means they would be visible to anyone who clones the repository and inspects the history.

The `2006_Bush_mapped.json` file contains a real faculty surname in its `document_uid` field. While it does not contain phone numbers, email addresses, or other direct contact PII, the surname itself is derived from a real faculty CV filename and constitutes PII under the project's core value ("No personally identifiable information ships to the public repository").

The `.gitignore` already has `**/.outputs/` on line 53, which is correct for preventing future tracking. The gap is solely in the history rewrite.

### Finding 2: v1.0.0 Tag Not on HEAD

The v1.0.0 tag was applied during Plan 03 Task 3. At that moment, HEAD was `66cdbc8` (the docs(02-02) commit). Immediately after, a final documentation commit `b9a754a` was added (docs(02-03) -- the SUMMARY.md, STATE.md, ROADMAP.md, REQUIREMENTS.md update for Plan 03 completion). The tag was not moved to follow.

This means:
- `git log --oneline -1 v1.0.0` returns `66cdbc8`
- `git log --oneline -1 HEAD` returns `b9a754a`
- `git log --oneline v1.0.0..HEAD` shows one commit: `b9a754a`

The tag is otherwise correctly formed: it is annotated (not lightweight), the tagger identity and timestamp are correct, and the message matches the plan specification.

Note: once the `.outputs/` purge is re-run, all commit hashes will change anyway, so the tag must be re-applied regardless. At that point it should be placed on the new HEAD.

---

## Human Verification Required

None. All gaps can be verified programmatically.

---

## Gaps Summary

Two gaps block full goal achievement:

**Gap 1 (Blocking -- GIT-03):** The history rewrite missed the `.outputs/` hidden directory. The git-filter-repo invocation removed `outputs/` (no dot prefix) but the repository contained `.outputs/` (with leading dot). Files in `src/unified_pipeline/core/.outputs/phase2_20cvs/` survive in 14 historical commits including `2006_Bush_mapped.json` which contains a real faculty surname. A targeted second rewrite pass is needed.

**Gap 2 (Minor -- VER-02):** The v1.0.0 tag is one commit behind HEAD. The documentation commit recording Plan 03's completion was added after the tag. The tag needs to move to HEAD -- which is naturally addressed by the re-purge in Gap 1 (since all hashes will change and the tag must be re-applied).

**Root cause:** Both gaps stem from the same Plan 02 execution: the filter-repo folder targets did not include `.outputs/` (hidden variant), and the tag was applied before the final documentation commit. The re-purge fixes both.

---

_Verified: 2026-03-23T01:44:40Z_
_Verifier: Claude (gsd-verifier)_
