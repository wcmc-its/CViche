# Dev workflow — the lay of the land

How work moves through this repo. Read this before starting a task, and before
telling a parallel session what to do.

If you are briefing another session or agent, the copy-paste block at the bottom
is the short version.

## The one-paragraph version

`dev` is the integration branch and, since 2026-08-06, the GitHub default branch.
Branch off `origin/dev`, open a PR into `dev`, wait for CI, merge it. `Closes #N`
now closes the issue for you. `main` is a release pointer and is not where
day-to-day work lands. There is also a local-only branch literally named
`integration`, which is a scratch tree for testing open PRs together — it is
never pushed and never the base of a PR.

## Branches

| Branch | What it is |
|---|---|
| `dev` | Integration branch **and the GitHub default branch** since 2026-08-06. All work targets this. |
| `main` | Release pointer. Not the day-to-day target, and well behind `dev`. |
| `integration` | **Local only.** `origin/dev` + every open PR, for testing combinations. Never pushed, never a PR base. |

Always branch from **freshly fetched** `origin/dev`:

```bash
git fetch origin
git worktree add -b fix/123-short-slug ~/worktrees/cviche-123 origin/dev
```

Never branch off a local `dev`/`main` that may have drifted, and never off
`integration` — that would smuggle unreviewed code from other PRs into yours.

## Pull requests

### Base every PR on `dev`. Do not stack.

A PR whose base is another feature branch costs more than it saves:

- Nothing in the chain can merge until the bottom one does, so one round of
  comments blocks the whole stack. The `#398` chain ran four deep — #514 → #515
  → #516 → #517 — and sat with all four `CHANGES_REQUESTED` at once, one of them
  already `CONFLICTING`.
- Every revision to the bottom PR rebases the three above it, and each rebase
  re-opens their diffs for re-review.
- None of them auto-close an issue. Of the eleven PRs merged 2026-08-05 → 08-07,
  **seven merged into a feature branch rather than `dev`**.

Land one, then rebase the next onto `dev`. Sequential is slower to start and much
faster to finish.

### Split by what has to land, not by what is convenient to write

Comment volume tracks lines changed, not defect density: #518 drew 50 substantive
comments over 16 files; #535 drew 8 over one 200-line script. A 3,000-line PR is
not reviewed three times as fast as three 1,000-line ones.

The one size complaint that recurs is about **file** size, not PR size, and it is
explicit (PR #397, 2026-07-24):

> "I'll review this file once it has been split into smaller, more manageable
> files. At over 4,000 lines of code, it's difficult to review thoroughly and
> maintain effectively in its current state."

### Replying to review comments

The standing verdict on most reviews is:

> "Please address the code review comments if possible. They are improvement
> suggestions, not bugs. If any of them cannot be addressed now, please leave a
> comment explaining why. I will merge the PR as is."

So a review is not a gate. The friction is in how we answer it. Two rules, both
written from complaints we actually caused:

1. **Never decline without a reason.** "Declined" with no explanation drew the
   same objection seven times across #514 and #517 — *"Declined without any
   explanation."* Say what you considered and why it does not apply here.
2. **File the issue before you reply, and name the real number.** Replying "will
   track this" and not filing is our most common failure: the #569 retrospective
   found **34 of 45 tracking promises were never filed**. Pointing at an issue
   that does not in fact cover the comment is the same failure with extra steps —
   *"I don't see this issue included in #494"* (#503). Open the issue, then paste
   its number.

### What reviews here consistently ask for

Across 152 substantive review comments on PRs #503, #509, #514–#518 and #535,
six asks account for most of the volume. Meeting them in the first push is
cheaper than answering them one comment at a time:

| Ask | Comments | Standing issue |
|---|---|---|
| Split the function — one responsibility, one level of abstraction | 24 | #577 |
| Builtin generics (`dict`, not `typing.Dict`) and full annotations | 23 | #533 |
| A dataclass or `TypedDict` instead of a bare `dict` + `.get()` chain crossing a function boundary | 18 | #567, #494 — now also `CODING_STANDARDS.md` §8.1 |
| No silently swallowed exception, no bare `except Exception` | 17 | — |
| A named constant or `Enum` instead of an inline taxonomy code or format literal | 16 | now `CODING_STANDARDS.md` §8.2 |
| No `print()` in `src/` — and mind the two parsers that consume stage output | 8 | #563 |

Those five standing issues absorb roughly 60% of everything written in review, so
a comment that maps onto one of them belongs there rather than in a new issue.

Nobody asks for tests in review. Testability appears only as an argument for
splitting a function, never as a standalone request — which means test coverage
is ours to decide, not something a reviewer will catch.

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

### After merging: `Closes #N` now does the closing

**Changed 2026-08-06.** `dev` became the GitHub default branch, so a PR merged
into `dev` auto-closes the issues its body names. Verified end to end:

```
PR #508  merged into dev  2026-08-06T14:51:19Z   body: "Closes #268"
issue #268  CLOSED 2026-08-06T14:51:20Z  reason=COMPLETED  closedBy=[508]
```

Earlier notes in this file said the opposite, and were correct until that date.
Anything merged before it never fired — #520 sat open for a day after #546
shipped its fix for exactly this reason.

Still true:

- **One `Closes #N` per issue.** Two issues need two keywords; a comma list
  closes only the first.
- **It does not fire from a stacked PR.** Auto-close triggers only on a merge
  into the default branch, so a PR based on another feature branch closes
  nothing, ever. See "Pull requests" above.
- **Check that it fired.** `gh issue view N --json state,closedByPullRequestsReferences`.
  A stale-open issue causes duplicated work: someone picks up something that
  shipped weeks ago.
- **A partial fix does not stay open at full scope.** Retitle the issue down to
  the precise remaining gap rather than leaving the original text standing.

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
