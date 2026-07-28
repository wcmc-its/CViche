# Dev workflow — the lay of the land

How work moves through this repo. Read this before starting a task, and before
telling a parallel session what to do.

If you are briefing another session or agent, the copy-paste block at the bottom
is the short version.

## The one-paragraph version

`dev` is the integration branch. Branch off `origin/dev`, open a PR into `dev`,
wait for CI, merge it, then close the issue **by hand**. `main` is the default
branch but is not where day-to-day work lands. There is also a local-only branch
literally named `integration`, which is a scratch tree for testing open PRs
together — it is never pushed and never the base of a PR.

## Branches

| Branch | What it is |
|---|---|
| `dev` | Integration branch. All work targets this. |
| `main` | GitHub default branch. Not the day-to-day target. |
| `integration` | **Local only.** `origin/dev` + every open PR, for testing combinations. Never pushed, never a PR base. |

Always branch from **freshly fetched** `origin/dev`:

```bash
git fetch origin
git worktree add -b fix/123-short-slug ~/worktrees/cviche-123 origin/dev
```

Never branch off a local `dev`/`main` that may have drifted, and never off
`integration` — that would smuggle unreviewed code from other PRs into yours.

## Merging

**We hold merge rights on `dev` as of 2026-07-25.** PRs no longer queue behind a
single reviewer. This is a change from older notes that describe Mahender as a
gate; he still reviews, but he is not a blocker.

Two rules that did not change:

1. **Only merge when the human asks for it.** Merge rights are not standing
   permission to merge your own work.
2. **CI must be actually green**, not "green locally". `ci.yml` runs on PRs to
   `dev` and `main`: `backend-tests`, `pipeline-tests`, `frontend-typecheck`,
   `type-check`. `deps-audit.yml` runs only when a requirements file changes.

### Before merging a *batch* of PRs, test them together

Each PR's CI tests it against `dev` alone — never against its siblings. "All
green and all MERGEABLE" tells you nothing about whether they can be merged
together.

Real example, 2026-07-25: eight open PRs, every one MERGEABLE with 4/4 green.
#441 and #445 still conflicted with each other, because both registered a new
lint at the same points. Worse, four of the conflict hunks were a *lint count* —
one branch said 14, the other 15, and the correct merged answer was 16. Taking
either side would have shipped a wrong number and a failing assertion.

So:

```bash
~/.claude/scripts/cviche-integration.sh          # rebuild integration
cd ~/worktrees/cviche-integration
python3 -m pytest src/unified_pipeline/tests/ -q
```

Conflicts reported by that script are real and will hit you at merge time. Merge
the independent PRs first, then merge `dev` into the conflicting branch, resolve,
push, let CI re-run, and merge.

### After merging: close the issue yourself

**A merge to `dev` never auto-closes anything.** GitHub only auto-closes issues
from the default branch (`main`). `Closes #N` in a PR body targeting `dev` does
nothing on merge.

This is the single biggest source of stale-open issues here, and stale-open
issues cause duplicated work: someone picks up an issue that shipped weeks ago.

```bash
gh issue close 418 -c "Landed on dev in PR #419. Closing manually: a merge to dev
does not auto-close, GitHub only auto-closes on the default branch (main)."
```

Every issue number needs its own `Closes` keyword in the PR body, and its own
manual close after. If a merge only partly satisfies an issue, do not leave the
full original scope open — retitle it down to the precise remaining gap.

## The `integration` branch

```bash
~/.claude/scripts/cviche-integration.sh            # rebuild: origin/dev + all open PRs
~/.claude/scripts/cviche-integration.sh --list     # dry run, changes nothing
~/.claude/scripts/cviche-integration.sh --with fix/my-branch   # include your branch too
```

- Branch name: `integration`. Worktree: `~/worktrees/cviche-integration`.
- The PR list is discovered from `gh`, so merged PRs drop out automatically.
- It is **disposable**: rebuild it, never commit to it. Anything committed there
  is lost on the next rebuild, by design.
- `rerere` is on, so a conflict you resolve once is remembered across rebuilds.
- When the PR queue is empty, `integration` == `origin/dev`. That is expected.

Use it to answer questions no single PR can: does my change still work once the
other five land, and do any of them collide?

## Issues first

File the issue before writing the fix, even when the fix is obvious. A good issue
carries symptom, root cause, evidence, and a fix sketch — the evidence is what
lets someone else pick it up, and what stops a plausible-but-wrong theory from
becoming a merged PR.

**Re-verify the reproduction on `origin/dev` before implementing any open issue.**
Some are already fixed and simply never got closed; some were filed against a
theory that later proved wrong.

## Testing

```bash
# pipeline suite — no env needed, ~4s
python3 -m pytest src/unified_pipeline/tests/ -q

# backend suite — needs dummy env, the factory raises without it
CVICHE_SESSION_SECRET=x DB_HOST=x DB_PORT=3306 DB_USER=x DB_PASSWORD=x DB_NAME=x \
  python3 -m pytest web_interface/backend/tests/ -q
```

A local pass is not evidence a change is done. CI is.

Leave one runnable check behind for non-trivial logic. For a CLI consumed by
another script, pin the *contract* — `scripts/doctor_one.py` and
`scripts/score_one.py` both have contract tests covering "stdout is exactly one
TSV line, diagnostics go to stderr, a failure exits non-zero". Copy that shape.

## Running a corpus batch

```bash
scripts/run_corpus_batch.sh [--doctor] <input_dir> [count] [results_dir] [model]
```

Sequential by design — one CV at a time. Each run already fans out several
concurrent LLM calls internally.

To run batches **in parallel**, give each one its own git worktree. This is not
stylistic: the script resolves outputs against fixed repo-root paths, and
`quality_score.json` is a single fixed filename rewritten per run, so two batches
sharing a repo root will attribute one CV's score to another. Give each batch a
disjoint input directory too (symlink farms work) — the runner always globs from
the earliest unrun CV, so pointing two at the same directory runs the same CVs
twice.

Launch long batches detached so they survive turn-end cleanup:

```bash
nohup scripts/run_corpus_batch.sh --doctor <dir> 25 </dev/null > batch.log 2>&1 & disown
```

Per-CV cost is ~$1 on average, up to ~$4 for a very large CV. A 100-CV batch is
roughly $110 and many hours. Cost is **not** in `summary.tsv` — it comes from the
`Running total:` lines in `<results>/logs/<cv>.log`.

**A pipeline blocked on Bedrock shows 0% CPU and writes no files for long
stretches. That is normal, not a hang.** Confirm with `lsof -p <pid> -a -i`: an
ESTABLISHED connection to an AWS host means it is waiting on a model response.

## Data and secrets

- Real CVs are PII. They live in gitignored paths, and `/*.docx` blocks the repo
  root because reproducing a run means dropping a real CV there.
- `web_interface/docker-compose.override.yml` is gitignored: it interpolates host
  AWS credentials.
- Never commit `.env`, credentials, or a real CV. Refer to run subjects by their
  6-character uid, not by name — that is the convention in `docs/analysis/`.

## Measurement landmines

These have each already cost someone a session:

- **Do not validate a rule with its own definition.** A check that re-implements
  the predicate under test always passes. Before quoting an acceptance check,
  swap its predicate for a constant — if it is still green, it was tautological.
- **Do not trust a single stage-3b roll.** Identical input has produced 278, 268
  and 324 entries. Confirm any classification finding across at least two runs.
- **Do not compare run outputs by naive string diff.** Stages 5c/5d reformat and
  lists renumber; this once produced 151 phantom "lost records", all artifacts.
- **Do not treat an unchanged doctor verdict as "no regression."**
- **Do not quote prevalence from run counts.** The S3 corpus is ~96 runs but only
  ~27 distinct CVs; one CV accounts for 44 of them. Collapse by content
  fingerprint first. The committed sample outputs are worse.
- **Do not touch the `table_N` string indices** in `extract_docx_structure` —
  stage 2 depends on them via `startswith` and sort keys. Fix table-index bugs at
  the consumer.

More detail in `docs/INVESTIGATING.md`.

## Briefing a parallel session

Copy-paste this, filling in the task:

```text
Repo: ~/Dropbox/GitHub/CViche. Read docs/DEV_WORKFLOW.md first — it is the
working agreement.

Your task: <issue number and one-line description>

Ground rules:
- Branch from freshly fetched origin/dev into your OWN worktree under
  ~/worktrees/ (outside Dropbox). Do not work in the main checkout: other
  sessions share it.
- Target: PR into dev. Do not merge; I will say when.
- Re-verify the issue reproduces on origin/dev before writing any fix. Some open
  issues are already fixed and just never got closed.
- CI must be green before you claim anything is done. A local test pass is not
  evidence. Tell me exactly which checks you ran.
- Never commit a real CV (PII), .env, or credentials.
- No AI attribution in commits or PR descriptions.
- If you find problems outside your diff, file separate issues rather than
  expanding this PR.

Do not touch these paths — other sessions and long-running batches use them:
  ~/worktrees/cviche-integration
  ~/worktrees/cviche-batch*        (only when a batch is running)
  ~/worktrees/batch-slices

Before you start, tell me: what you believe the root cause is, and how you will
prove the fix works without breaking the other ~27 distinct CVs in the corpus.
```

Two things worth deciding before you split work across sessions:

**Not everything parallelises.** Two changes that both add something to the same
registry will conflict even though each is "purely additive" — that is exactly
how #441 and #445 collided. Give a second session work that touches *different
files*, or accept that one will need a rebase.

**Sequence work whose measurement depends on other work.** If PR A changes the
data PR B is measured against, landing A first makes every later number
comparable. Running them concurrently means measuring against a moving target.
