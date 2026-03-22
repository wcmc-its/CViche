# Phase 2: History Rewrite and Tagging - Research

**Researched:** 2026-03-22
**Domain:** Git history rewriting with BFG Repo Cleaner
**Confidence:** HIGH

## Summary

Phase 2 uses BFG Repo Cleaner v1.15.0 to purge all PII and bloat from git history, then tags the clean result as v1.0.0. The repository currently has 18 commits with a .git directory of 275 MB. The working tree was sanitized in Phase 1 (git rm --cached removed 60,000+ files from tracking), but the historical blobs remain in packfiles. BFG operates on a bare mirror clone, rewrites history to remove targeted blobs, then the cleaned history replaces the original.

A critical pre-BFG step was discovered during research: three `src/logs/*.jsonl` files (14 MB total, including the 13 MB `classifications.jsonl` with PII-risk content) are still tracked in HEAD. BFG's HEAD protection mechanism will preserve these blobs if they are not removed before the rewrite. These files must be `git rm`-ed and committed, and `src/logs/` must be added to `.gitignore`, before BFG runs.

**Primary recommendation:** Remove `src/logs/*.jsonl` from tracking and update `.gitignore` before running BFG. Then use a combination of `--delete-files` (extension globs) and `--delete-folders` (folder name matching) across multiple BFG passes on the mirror clone. Follow with `git reflog expire --expire=now --all && git gc --prune=now --aggressive`. Require manual user approval before tagging.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- Use BFG Repo Cleaner (installed via Homebrew: `brew install bfg`)
- BFG only -- no git filter-repo second pass needed
- No PII exists in commit messages (all 16 commits are Phase 1/GSD planning work)
- BFG operates on a bare clone; we'll work on a mirror then replace
- Target by path + extension globs (not size threshold)
- Paths to purge from history: `data/`, `archive/`, `web_interface/uploads/`, `web_interface/frontend/node_modules/`, `web_interface/frontend/dist/`, `src/unified_pipeline/core/prompt_logs/`, `web_interface/backend/prompt_logs/`, `web_interface/outputs/`, `src/logs/`, `key_files/` (except cv_template_wcm.docx)
- Extensions to purge: `*.pdf`, `*.docx` (except cv_template_wcm.docx), `*.sqlite`, `*.db`, `*.tar.gz`, `*.key`
- Specific files: `web_interface/backend/auth_config.yaml`, `web_interface/backend/cviche.db`, `web_interface/backend/cviche_dev.db`, `web_interface/backend/pipeline.db`
- `classifications.jsonl` (13MB, src/logs/) -- purge (Claude's discretion: contains LLM outputs with potential CV content, PII risk)
- Logo file (CViche - Logo.key, 11MB) -- purge from history, keep on disk, copy to Projects/ first
- Before rewrite: copy logo and large design/business artifacts to `~/Dropbox/Projects/CViche - Planning/`
- Operational data (prompt logs, classifications, databases) does NOT need external backup -- can be regenerated
- Full mirror clone before rewrite: `git clone --mirror` to `~/Dropbox/Projects/CViche - Planning/repo-backup-pre-rewrite.git`
- Preserves complete original history (~275MB) as a restore point
- Dry run BFG first (report mode) to review what would be purged before executing
- Only proceed to actual rewrite after reviewing dry run output
- Automated blob scan: enumerate ALL blobs in rewritten history, verify none match targeted extensions/paths
- Verify .git directory size < 50 MB after gc/repack
- Verify current working tree is intact (all tracked files still present and functional)
- Manual sign-off required before tagging -- show verification results, wait for explicit approval
- Annotated tag: `git tag -a v1.0.0 -m "..."` with release message
- Tag applied only after verification passes AND user approves
- Tag goes on the final clean commit (HEAD after rewrite + repack)

### Claude's Discretion
- Exact BFG command flags and ordering of operations
- Whether to run multiple BFG passes (one per category) or a single pass with combined glob
- Git gc/repack strategy (aggressive vs standard)
- Exact wording of the v1.0.0 annotated tag message
- Implementation of the automated blob scan verification script
- Whether `*.key` should be a blanket extension or specifically target the logo file path

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| GIT-03 | Git history rewritten to purge PII (faculty CVs, prompt logs with CV text, SQLite databases with user data, auth config with real emails) using BFG Repo Cleaner or git filter-repo | BFG v1.15.0 via Homebrew; extension-based and folder-based deletion; HEAD protection mechanism preserves template files; pre-BFG cleanup required for src/logs/ |
| VER-02 | Git tag v1.0.0 applied to the clean public release commit | Annotated tag `git tag -a v1.0.0 -m "..."` on HEAD after rewrite + gc + verification + user approval |

</phase_requirements>

## Standard Stack

### Core
| Tool | Version | Purpose | Why Standard |
|------|---------|---------|--------------|
| BFG Repo Cleaner | 1.15.0 | Rewrite git history to remove files/blobs | Fastest tool for bulk history rewriting; 10-720x faster than git filter-branch; well-maintained |
| OpenJDK | 17+ | BFG runtime dependency | BFG is a JVM application (Scala); Java 17 already installed on this system |
| Git | (system) | Mirror clone, gc, repack, tagging | Standard VCS operations |

### Supporting
| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| Homebrew | (system) | Install BFG | `brew install bfg` -- not yet installed |
| git verify-pack | (built-in) | Enumerate all blobs for verification | Post-rewrite blob audit |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| BFG | git filter-repo | git filter-repo supports path-based matching; BFG only matches filenames. User locked BFG decision. For this repo, filename-based matching is sufficient. |
| BFG | git filter-branch | Much slower (10-720x); more complex syntax; officially deprecated by Git project |

**Installation:**
```bash
brew install bfg
```

**Version verification:** BFG 1.15.0 is the latest stable release (confirmed via `brew info bfg`). Java 17 is confirmed available.

## Architecture Patterns

### Recommended Operation Sequence
```
1. Pre-BFG cleanup (fix HEAD)
   a. Add src/logs/ to .gitignore
   b. git rm src/logs/*.jsonl
   c. Commit
2. Artifact preservation
   a. Copy logo + design files to ~/Dropbox/Projects/CViche - Planning/
3. Safety backup
   a. git clone --mirror . ~/Dropbox/Projects/CViche - Planning/repo-backup-pre-rewrite.git
4. BFG execution (on mirror)
   a. Create working mirror: git clone --mirror . /tmp/cviche-clean.git
   b. Run BFG passes (multiple or single)
   c. git reflog expire + git gc --aggressive
5. Replace original repo
   a. Back up original .git/
   b. Replace with cleaned mirror
   c. git reset --hard HEAD
6. Verification
   a. Automated blob scan
   b. .git size check
   c. Working tree integrity check
7. Tagging (after manual approval)
   a. git tag -a v1.0.0
```

### Pattern 1: BFG HEAD Protection
**What:** BFG v1.15.0 automatically protects the latest commit (HEAD) on each branch. Files matching deletion criteria that exist in HEAD will NOT be deleted from HEAD, and their blob IDs will be preserved across all earlier commits.
**When to use:** Always -- this is default BFG behavior.
**Critical implication:** Any file that should be purged MUST be removed from HEAD (via `git rm` + commit) BEFORE running BFG. Files still in HEAD will survive the rewrite.

### Pattern 2: Filename-Only Matching
**What:** BFG's `--delete-files` and `--delete-folders` match on filename/foldername only, NOT on full paths. `--delete-files auth_config.yaml` deletes ALL files named `auth_config.yaml` regardless of directory.
**When to use:** This is the only mode available in BFG. Safe for this repo because targeted filenames are unambiguous.
**Example:**
```bash
# This works -- matches all .pdf files by name
bfg --delete-files '*.pdf' repo.git

# This FAILS -- BFG cannot match paths
bfg --delete-files 'data/sample_cvs/*.pdf' repo.git
# Error: Can only match on filename, NOT path
```

### Pattern 3: Multiple BFG Passes
**What:** Run BFG multiple times on the same mirror, each targeting a different category.
**When to use:** When you need different deletion strategies (extensions vs folders vs specific files).
**Example:**
```bash
# Pass 1: Extensions
bfg --delete-files '*.{pdf,docx,sqlite,db,key,jsonl}' repo.git

# Pass 2: Specific files
bfg --delete-files 'auth_config.yaml' repo.git

# Pass 3: Folders
bfg --delete-folders '{data,archive,node_modules,uploads,prompt_logs,dist,outputs,logs}' repo.git

# Pass 4: Large tar archives
bfg --delete-files '*.tar.gz' repo.git
```

### Anti-Patterns to Avoid
- **Running BFG on the working repo directly:** Always operate on a bare mirror clone. BFG requires a bare repository.
- **Skipping the pre-BFG HEAD cleanup:** If sensitive files remain in HEAD, BFG's protection mechanism will preserve them throughout all history. This is the most common BFG mistake.
- **Using `--no-blob-protection`:** This flag removes HEAD protection entirely, which could damage files you want to keep. Not needed for this repo.
- **Forgetting reflog expiry:** After BFG rewrites history, old commits still exist via reflog. Must run `git reflog expire --expire=now --all` before gc.
- **Running gc without --aggressive:** Standard gc may not fully repack. Use `--aggressive` for maximum compression after a major rewrite.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| History rewriting | Custom git filter-branch scripts | BFG Repo Cleaner | 10-720x faster; handles edge cases (merge commits, reflogs) correctly |
| Blob enumeration | Manual commit-by-commit inspection | `git verify-pack -v .git/objects/pack/*.idx` | Exhaustive enumeration of every blob in packfiles |
| Size-based cleanup | Manual blob identification | `bfg --strip-blobs-bigger-than SIZE` | Automated; respects HEAD protection |

**Key insight:** Git history rewriting is deceptively complex. Merge commits, grafts, reflogs, and packfile references all need correct handling. BFG handles all of these automatically.

## Common Pitfalls

### Pitfall 1: Files Still Tracked in HEAD (CRITICAL)
**What goes wrong:** BFG preserves blobs that exist in the HEAD (protected) commit. If you run BFG with `--delete-files *.jsonl` while `src/logs/classifications.jsonl` is still tracked in HEAD, the 13MB blob survives in every historical commit.
**Why it happens:** BFG's HEAD protection is a safety feature -- it prevents accidentally breaking your current working state.
**How to avoid:** Before ANY BFG operation, verify that HEAD contains ONLY files you want to keep. Run `git ls-files` and audit against the purge target list.
**Warning signs:** After BFG, `git verify-pack` still shows large blobs. The .git directory is still large.
**This repo's specific issue:** `src/logs/calibration_issues.jsonl` (273K), `src/logs/classifications.jsonl` (13M), and `src/logs/validation_events.jsonl` (921K) are still tracked in HEAD as of the current commit.

### Pitfall 2: .gitignore Gap for src/logs/
**What goes wrong:** The `.gitignore` created in Phase 1 does not cover `src/logs/` or `*.jsonl`. Even after `git rm`, new files could be accidentally committed to this directory.
**Why it happens:** Phase 1's .gitignore focused on `**/prompt_logs/`, `**/outputs/`, `*.db`, etc. but `src/logs/` is a different directory name.
**How to avoid:** Add `src/logs/` (or `**/logs/` or `*.jsonl`) to `.gitignore` before running `git rm` and committing.
**Warning signs:** `git check-ignore -v src/logs/classifications.jsonl` returns nothing.

### Pitfall 3: Mirror Clone vs Regular Clone
**What goes wrong:** Using `git clone` (without `--mirror`) creates a regular clone with a checkout. BFG requires a bare repository.
**Why it happens:** Habit of using regular clone.
**How to avoid:** Always use `git clone --mirror` for BFG operations. The result is a bare repo (top-level contents look like what's normally inside `.git/`).
**Warning signs:** BFG error about non-bare repository.

### Pitfall 4: Forgetting to Replace the Original .git
**What goes wrong:** After BFG cleans the mirror, the original repository still has the dirty history. You must replace the original `.git/` directory or re-clone from the cleaned mirror.
**Why it happens:** The BFG docs focus on pushing to a remote, not replacing a local repo.
**How to avoid:** Plan the replacement strategy in advance. For a local-only repo (no remote), the approach is: (1) back up original `.git/`, (2) copy cleaned mirror contents into `.git/`, (3) `git reset --hard HEAD`.
**Warning signs:** After "completing" BFG cleanup, `du -sh .git` still shows 275MB.

### Pitfall 5: Template .docx Files Accidentally Deleted
**What goes wrong:** Running `--delete-files '*.docx'` without understanding HEAD protection could lead to concern about template files being lost.
**Why it happens:** BFG's filename-only matching means ALL .docx files match the pattern.
**How to avoid:** This is actually safe because the two template .docx files (`key_files/wcm_cv_template_faculty_october_2022_final.docx` and `src/unified_pipeline/cv_parser/cv_template_wcm.docx`) ARE in HEAD. BFG's HEAD protection will preserve their blobs throughout history. No special handling needed.
**Warning signs:** None -- this works correctly by default.

### Pitfall 6: Backup Before Destructive Operation
**What goes wrong:** History rewrite is irreversible. Without a backup, the original history is lost forever.
**Why it happens:** Overconfidence in the rewrite process.
**How to avoid:** Create the mirror backup FIRST: `git clone --mirror . ~/Dropbox/Projects/CViche - Planning/repo-backup-pre-rewrite.git`. This preserves the complete 275MB original history as a restore point.
**Warning signs:** No backup file exists before BFG runs.

## Code Examples

### Pre-BFG: Fix HEAD by removing src/logs/ files
```bash
# Add src/logs/ to .gitignore
echo -e "\n# Log data files (PII risk)\nsrc/logs/" >> .gitignore

# Remove from tracking (files stay on disk)
git rm --cached src/logs/calibration_issues.jsonl src/logs/classifications.jsonl src/logs/validation_events.jsonl

# Commit
git commit -m "chore: remove src/logs/ from tracking before history rewrite"
```

### Backup: Mirror clone for safety
```bash
git clone --mirror . ~/Dropbox/Projects/"CViche - Planning"/repo-backup-pre-rewrite.git
```

### Copy artifacts before purge
```bash
cp archive/business/marketing/"CViche - Logo.key" ~/Dropbox/Projects/"CViche - Planning/"
cp archive/business/marketing/header-logo.png ~/Dropbox/Projects/"CViche - Planning/"
cp archive/business/marketing/headerbg.png ~/Dropbox/Projects/"CViche - Planning/"
```

### BFG: Create working mirror and run cleanup
```bash
# Create working mirror
git clone --mirror . /tmp/cviche-clean.git
cd /tmp/cviche-clean.git

# Pass 1: Delete by extension
bfg --delete-files '*.{pdf,docx,sqlite,db,key,jsonl,tar.gz}' .

# Pass 2: Delete specific sensitive files
bfg --delete-files 'auth_config.yaml' .

# Pass 3: Delete entire folder contents
bfg --delete-folders '{data,archive,node_modules,uploads,prompt_logs,prompt_logs_debug,dist,outputs,logs}' .
```

### Post-BFG: Garbage collection
```bash
cd /tmp/cviche-clean.git
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

### Replace original .git
```bash
# Back up original
mv /path/to/CViche/.git /path/to/CViche/.git.bak

# Copy cleaned mirror
cp -a /tmp/cviche-clean.git /path/to/CViche/.git

# Reset working tree to match
cd /path/to/CViche
git reset --hard HEAD
```

### Verification: Exhaustive blob scan
```bash
#!/bin/bash
# Enumerate ALL blobs in history and check for targeted content
echo "=== Checking for surviving sensitive blobs ==="

# Get all blob IDs and their paths
git rev-list --all --objects | while read hash path; do
  if [ -n "$path" ]; then
    # Check extensions
    case "$path" in
      *.pdf|*.sqlite|*.key|*.jsonl|*.tar.gz)
        echo "FAIL: $path ($hash)"
        ;;
    esac
    # Check paths
    case "$path" in
      data/*|archive/*|*/node_modules/*|*/uploads/*|*/prompt_logs/*|*/dist/*)
        echo "FAIL: $path ($hash)"
        ;;
    esac
    # Check specific files
    case "$path" in
      */auth_config.yaml|*/cviche.db|*/cviche_dev.db|*/pipeline.db)
        echo "FAIL: $path ($hash)"
        ;;
    esac
    # Check .docx -- only templates should survive
    case "$path" in
      *.docx)
        case "$path" in
          */cv_template_wcm.docx|*/wcm_cv_template_faculty_october_2022_final.docx)
            # These are expected -- template files
            ;;
          *)
            echo "FAIL: unexpected .docx: $path ($hash)"
            ;;
        esac
        ;;
    esac
    # Check .db
    case "$path" in
      *.db)
        echo "FAIL: $path ($hash)"
        ;;
    esac
  fi
done

echo "=== .git directory size ==="
du -sh .git

echo "=== Tracked file count ==="
git ls-files | wc -l
```

### Tagging
```bash
git tag -a v1.0.0 -m "v1.0.0 - Initial public release

CViche: AI-powered CV parsing pipeline for WCM template format.

This release marks the first clean public version with all PII
purged from repository history."
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| git filter-branch | BFG Repo Cleaner or git filter-repo | 2013+ (BFG), 2019+ (filter-repo) | git filter-branch is officially deprecated by Git project |
| BFG for path-based deletion | git filter-repo for path-based deletion | 2019+ | BFG cannot match paths, only filenames. filter-repo fills this gap. Not needed for this repo. |

**Deprecated/outdated:**
- `git filter-branch`: Officially deprecated. Slow, error-prone, complex syntax. Use BFG or git filter-repo instead.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Shell scripts (bash verification) |
| Config file | None -- verification is command-based |
| Quick run command | `du -sh .git && git ls-files \| wc -l` |
| Full suite command | Custom blob scan script (see Code Examples) |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| GIT-03 | No PII blobs in any historical commit | automated | Blob scan script: enumerate all objects, check for targeted extensions/paths | Wave 0 |
| GIT-03 | .git directory under 50 MB | automated | `du -sh .git` -- parse output, assert < 50M | Wave 0 |
| GIT-03 | auth_config.yaml purged from history | automated | `git log --all --diff-filter=ACMR --name-only -- "*/auth_config.yaml" \| grep -v example` | Wave 0 |
| VER-02 | v1.0.0 tag exists on HEAD | automated | `git tag -l v1.0.0` returns non-empty | Wave 0 |
| VER-02 | Tag is annotated with message | automated | `git cat-file -t v1.0.0` returns "tag" | Wave 0 |

### Sampling Rate
- **Per task commit:** `du -sh .git && git ls-files | wc -l`
- **Per wave merge:** Full blob scan script
- **Phase gate:** Full blob scan + size check + tag verification green before verify-work

### Wave 0 Gaps
- [ ] Blob scan verification script -- needs to be created (see Code Examples section)
- [ ] .gitignore update for `src/logs/` -- must be done before BFG

## Open Questions

1. **BFG dry-run capability**
   - What we know: The user requested a dry run before actual execution. BFG's official docs do not document a `--dry-run` flag.
   - What's unclear: Whether BFG outputs a report of what it WOULD do. BFG does print a summary after execution showing what was cleaned.
   - Recommendation: Run BFG on the mirror clone (not the original). The mirror IS the dry run -- if results look wrong, discard the mirror and try again. The original repo is untouched until the explicit replacement step. Print BFG's output for user review before proceeding.

2. **Exact replacement strategy for .git directory**
   - What we know: BFG operates on a bare mirror. The result must replace the working repo's history.
   - What's unclear: Whether to copy the mirror into `.git/` or re-clone from the mirror.
   - Recommendation: Re-clone approach is safest: after BFG + gc on mirror, do `git clone /tmp/cviche-clean.git /tmp/cviche-working`, then replace original repo contents. This guarantees a clean working tree.

3. **Whether *.key should be blanket or path-specific**
   - What we know: Only one `.key` file exists in history (`archive/business/marketing/CViche - Logo.key`). It's not in HEAD.
   - What's unclear: Whether future `.key` files (e.g., SSH keys accidentally committed) would be caught.
   - Recommendation: Use blanket `*.key` in BFG deletion. The extension is commonly associated with key/certificate files anyway, and no legitimate `.key` files need to stay in history.

## Sources

### Primary (HIGH confidence)
- [BFG Repo-Cleaner official site](https://rtyley.github.io/bfg-repo-cleaner/) - Command syntax, usage steps, HEAD protection behavior
- [BFG GitHub Issue #53](https://github.com/rtyley/bfg-repo-cleaner/issues/53) - HEAD protection preserves blobs (not just trees) in v1.15.0
- [BFG GitHub Issue #79](https://github.com/rtyley/bfg-repo-cleaner/issues/79) - Confirmed: BFG cannot match on paths, only filenames
- [BFG GitHub Issue #187](https://github.com/rtyley/bfg-repo-cleaner/issues/187) - Workarounds for path-based deletion (not needed for this repo)
- Homebrew `brew info bfg` - v1.15.0, requires openjdk
- Local git history analysis (18 commits, 275 MB .git, 60,397 unique historical file paths)

### Secondary (MEDIUM confidence)
- [DEV Community: Shrinking your git repository with BFG](https://dev.to/jakecarpenter/shrinking-your-git-repository-with-bfg-repo-cleaner-145e) - Step-by-step local mirror workflow
- [Git GC documentation](https://git-scm.com/docs/git-gc) - `--aggressive --prune=now` flags

### Tertiary (LOW confidence)
- None -- all findings verified against official sources or direct local testing

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - BFG v1.15.0 confirmed via Homebrew; Java 17 confirmed installed; all commands verified against official docs
- Architecture: HIGH - Operation sequence validated against BFG's documented behavior; HEAD protection behavior confirmed via GitHub issues
- Pitfalls: HIGH - Critical src/logs/ issue discovered through direct local git inspection; all pitfalls verified empirically

**Research date:** 2026-03-22
**Valid until:** Indefinite -- BFG 1.15.0 is stable (last release ~2023, no breaking changes expected for a mature tool)
