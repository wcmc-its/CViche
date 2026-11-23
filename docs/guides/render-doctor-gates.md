# Render and doctor gates

Corpus A/B gates for stage-6 render changes and `run_doctor` changes. Neither
existed as committed, tested code until issue #584 -- working prototypes had
been built and reused across several investigation sessions
(`docs/analysis/HANDOFF-backlog-strategy-2026-08-08.md`,
`docs/analysis/HANDOFF-wave1-completion-2026-08-11.md`) but only ever lived
on one contributor's machine. This is that work, formalized, with five known
defects in the prototypes fixed (one more found while fixing the others) and
every fix backed by a control that actually fires.

## TL;DR

```bash
# 1. Build a farm: any directory shaped like src/unified_pipeline/outputs/,
#    holding stage_4/5/5b/5c/5d JSON for the CVs you want to gate with.
#    Reuses whatever local pipeline runs already produced -- no new LLM
#    calls needed for either gate.

# 2. Render both arms (once per code version you're comparing)
PYTHONPATH=<arm>/src python3 scripts/render_gate.py <farm> out_a
PYTHONPATH=<arm-with-your-change>/src python3 scripts/render_gate.py <farm> out_b

# 3. Compare
python3 scripts/render_gate_compare.py out_a out_b

# 4. Doctor gate, same shape
PYTHONPATH=<arm>/src python3 scripts/doctor_gate.py <farm> a.json --source-dir <source-docx-dir>
PYTHONPATH=<arm-with-your-change>/src python3 scripts/doctor_gate.py <farm> b.json --source-dir <source-docx-dir>
python3 scripts/doctor_gate_compare.py a.json b.json
```

Both `_compare` scripts exit 0 only on a real PASS. Anything else -- a real
difference, a crashed render, a missing/unreadable input -- exits non-zero.

## One command: the controls and the gate together (#783)

```bash
python3 scripts/render_gate_controls.py <farm> --source-dir <source-docx-dir> \
  --base <base-checkout> --head <head-checkout> --work <scratch-dir> \
  [--manifest <farm-manifest.json>] [--allow-missing-sources] [--summary FILE]
```

Both arms are whole checkouts (a `git worktree` or a `git archive` of each
commit), not just `src/`: without `key_files/` stage 6 falls back to a
template with no grant tables and nothing raises. The script renders head
twice, base once and a mutated copy of head once, doctors three of those, and
prints the table the control logs below were written by hand:

| control | what it does | rules out |
|---|---|---|
| determinism | head rendered twice and doctored twice; both must be `CHANGED 0`, and the renders must not straddle a new year | the differ reporting noise |
| crash guard | each render directory is seeded with a stale `_wcm.docx` that must be gone afterwards; a copy of a clean arm whose index says every uid failed, with its docx left in place, must be refused | comparing files that prove nothing |
| real-CV rendering | every arm renders N of N farm CVs and exits 0; every doctor run (head twice, base once) reports N of N, records no failure and exits 0; with `--source-dir`, a source docx was found for N of N (fewer passes only with `--allow-missing-sources`, for a local farm with PDF-sourced runs) | an empty or partial arm reading as clean |
| mutation | a copy of head with the PERSONAL DATA work-email write removed; the CVs that change must be exactly the ones whose head render has a work email | the differ being blind |

The mutation is the 2026-09-07 control's shape (a real behaviour removed,
the deltas exactly the CVs it serves), moved to a line that every farm
exercises: the #550 write-back it used then only fires with `--source-dir`.
The mutation is applied to a copy under `--work`, never to the checkout, and
an anchor that no longer matches exactly once fails the control.

Then base vs head: paragraphs added and removed, fingerprint changes and
doctor findings gained and lost, per run and per lint. The doctor reads each
arm's own fresh render as `stage_6_wcm_documents`, so lints gated on the
rendered docx (`table_shape` among them) run too.

Exit 0: every control passed and head equals base. Exit 1: every control
passed and head differs (read the table). Exit 2: no evidence. That is a
failed control, a base-vs-head comparison that refused an arm or did not
cover every CV in both (a failed arm, not a change in head), a crash, or a
bad invocation. A doctor lint that crashed, or found an input unreadable, on
any CV in either arm fails the real-CV control, and the row names the lint
(no uid). run_doctor records both as ordinary findings (`lint <name>
crashed: <class>`, and `status: unreadable`), not as a `_failed` entry, so
without this a lint that crashed in one arm would read as head differing.
A CV whose doctor report is not a JSON object fails it too, and a
`SystemExit` raised inside the run is a crash like any other exception.

Everything it prints, and writes to `--summary`, carries no CV text: counts,
uids and lint names. Corpus uids come from real filenames and contain owners'
names, and no shape test separates them from a document uid (invented
examples of the real shapes: `QX_ZRV`, `ZQXVWB_CV`, `Zqxvw7_cv`). So uids
are printed by allowlist: a uid appears as it is only when `--manifest` (the
farm's `manifest.json`) lists it and it has the shape the web app generates
(six of A-Z0-9). Every other uid is
printed as `h:` plus 8 hex characters of its sha256. Without `--manifest`,
every uid is hashed. An exception of any class prints only its class name;
its traceback goes to `--work/controls_traceback.log`. The render and doctor
logs, which name full uids, stay under `--work`.

Renders take about 9 s per CV on dev as of 2026-11-22, not the 1.7 s measured
in August; four arms run side by side (`--jobs`, default 4).

## What each script does, and why it exists in this shape

- **`render_gate.py`** -- renders every CV in a farm through stage 6 for one
  code arm. `call_llm` is monkeypatched to raise (both call sites in stage 6
  are try/except guarded), so this needs no API key and no network. ~1.7s/CV.
- **`render_gate_compare.py`** -- two independent checks per CV: a paragraph
  text diff (read from raw `w:t` XML nodes, not `python-docx` `Paragraph.text`
  -- see `#461`, python-docx drops tracked-change runs and under-reports by
  10-19%) and a c14n fingerprint of the whole `word/document.xml`, which
  catches formatting the paragraph check can't see (styles, run properties,
  `numPr`/list level, table borders and parentage, every tracked-change
  deletion). PASS only at zero deltas on both.
- **`doctor_gate.py`** -- runs `run_doctor()` over every CV in a farm, using a
  symlink view per uid (`run_doctor` expects a per-run directory layout, not
  a flat farm).
- **`doctor_gate_compare.py`** -- byte-for-byte finding comparison after
  normalising the harness's own per-uid work-dir path out of each report.

## The five known defects (issue #584), and what fixed them

| # | Defect | Fix |
|---|---|---|
| 1 | `gate_render.py` never cleared its output dir; a render that crashed every CV left the *previous* arm's docx in place, and a naive compare read that as PASS | `render_gate.py` wipes `OUT` before rendering and exits non-zero if any render failed. `render_gate_compare.py` additionally reads both arms' `_render_index.json` and refuses to compare if either recorded a failure -- belt and suspenders, in case someone compares two `out_dir`s built some other way |
| 2 | Paragraph-only diff is blind to the entire formatting defect class (list levels, table borders, tracked-change deletions) | Added the c14n fingerprint check (above) |
| 3 | Stage 6 stamps `Date of preparation: <today>` and two more `datetime.now()` call sites bucket content by calendar year -- a cross-midnight A/B looks like a regression | Not fixable in a gate script: this is a stage-6 behaviour. Documented prominently; the fingerprint check masks the *stamp* (see below) but the year-boundary bucketing effect is real and requires same-day arms |
| 4 | `doctor_gate.py` had no way to attach the source `.docx`, so both segmentation lints (`segmentation`, `missed_headers`) silently skip on every uid | Added `--source-dir`, which symlinks `<uid>*.docx` in for `run_doctor`'s `source=` param |
| 5 | (as originally filed) `doctor_compare.py` anchored path normalisation on the output filename stem | Turned out to already be fixed in the version of the prototype that existed locally -- see "Corrections" below for what was **actually** still broken |

## Corrections made while building this

**Defect 5 as filed was stale.** The uncommitted prototype's `norm()` already
anchored on the `_work` path *suffix*, not a specific stem. What was still
broken, found by actually running the determinism control: the regex
required a **leading `/`** immediately before the work-dir segment, which a
**relative** invocation (`scripts/doctor_gate_compare.py a.json b.json` run
from a repo checkout with relative paths -- the natural way to invoke it)
never has. Two identical doctor runs, compared with relative paths, reported
`CHANGED 66` (100%) -- the same failure mode defect 5 was originally about,
just triggered by path style rather than filename stem. Fixed by making the
leading path segments optional in the regex. See `norm()`'s docstring in
`doctor_gate_compare.py` and `scripts/test_render_doctor_gates.py`.

**PII handling.** Any farm built from real harvested CVs is PII. Working
data for this went in `<repo>/scratch/`, which was **not** covered by the
existing `.gitignore` (`/*.docx` is root-level only; `**/outputs/` doesn't
match a `scratch/` dir holding copies). Added `/scratch/` and `**/scratch/`
to `.gitignore` before putting anything there. `git status` and
`git check-ignore -v` were both used to confirm before every commit in this
PR.

## Control log (2026-08-11, 52-66 CV local farm)

Not the historical 100-CV farm (the four batch worktrees it was built from
were already cleaned up) -- built from whatever local pipeline runs had
already produced under `src/unified_pipeline/outputs/` plus matching source
`.docx` from `web_interface/uploads/`. Honest count: 66 CVs had stage-4
output, 52 rendered cleanly (stage 6 crashed on 12, unrelated to this PR --
`_render_index.json` from that run has the failed uids and the crashes are
pre-existing, not introduced here), 58 had a locally-available source docx.

**render_gate / render_gate_compare:**
- Determinism (same farm, same code, rendered twice): `paragraph CHANGED 0`,
  `fingerprint CHANGED 0` -- PASS.
- Mutation (`_set_font` default `Arial` -> `Calibri`, reverted after):
  `paragraph CHANGED 0`, `fingerprint CHANGED 52` -- FAIL, exactly the shape
  predicted: invisible to text, caught by every single CV's fingerprint.
- Crash guard (arm B's `_render_index.json` rewritten to report every uid
  failed, stale docx files from a clean run left in place to simulate the
  original reused-dir bug): refused to compare, FAIL, without reading a
  single docx.

**doctor_gate / doctor_gate_compare:** `CHANGED`/`PASS`/`FAIL` below is driven by `findings` only -- `metrics` (#816) differences are reported per uid/key but never fail the gate (doctor_gate_compare.py's module docstring).
- `--source-dir` on vs. off, same farm: `missed_headers` 0 live / 66 skipped
  -> 46 live / 8 skipped; `segmentation` 0 live / 66 skipped -> 21 live / 8
  skipped. Every other lint identical. Confirms defect 4's fix directly.
- Determinism (same farm, same code, doctored twice, `--source-dir` both
  times): `CHANGED 0` -- PASS (this is the run that caught the relative-path
  regex bug above; failed before the fix, passed after).
- Mutation (`UNDER_EXTRACTION_MIN_CHARS` `800` -> `2500`, reverted after):
  `CHANGED 13` of 66 -- FAIL. (`TABLE_SHAPE_WARN_DEFECTS`, the historically
  documented control, is inert on this farm because it has no rendered
  `stage_6_docx` output at all -- table_shape needs a farm built with
  `render_gate.py` output symlinked in too, not covered by this control log.)
- Crash guard (`_failed` key injected into one arm's report): refused to
  compare, FAIL.

## Control log (2026-09-07, 66 CV local farm, #740)

Same farm as above (`src/unified_pipeline/outputs/`, 66 uids with stage-4
output), but all 66 now render cleanly -- the 12 crashes the 2026-08-11 log
recorded are gone. Source documents came from `data/sample_cvs/word/` via
`--source-dir`, which is the flag #740 added. Every arm was rendered on the
same day, from a worktree root, off the merge result rather than the branch
tip.

Run as `PYTHONPATH=<arm>/src python3 scripts/render_gate.py <farm> <out>
--source-dir data/sample_cvs/word`, compared with `render_gate_compare.py`.

- **Real CV rendering.** 66/66 rendered, exit 0, in every arm below. The
  per-uid `source_docx` notice resolved a document for 65; `web08` has none
  in this corpus and is recorded as `not found` rather than rendering as if
  the flag were absent.
- **Determinism** (same farm, same code, rendered twice): `paragraph
  CHANGED 0`, `fingerprint CHANGED 0` -- PASS. This is the run that makes
  every number below mean something.
- **`origin/dev` merge control** (branch tip vs the same branch merged with
  19 commits of `dev`, including `stage6/parsing/text.py` and
  `stage6/sections/board_certification.py`): `CHANGED 0`. The A/B is
  measuring this PR, not the merge.
- **Entry-point control** (`--source-dir` renders routed through
  `run_stage6(original_doc_path=...)` instead of the private
  `WCMTemplateGenerator` construction they used before): `paragraph CHANGED
  0`, `fingerprint CHANGED 0` -- PASS. The gate now calls what production
  calls, and calling it changes nothing.
- **Mutation** (the #550 write-back removed, restored after): `paragraph
  CHANGED 3`, `fingerprint CHANGED 3` -- FAIL, and the deltas are exactly
  the five values the write-back exists to carry: a work email on farm
  2003; a business address, an office phone and a work email on farm
  2054; a street address on `NSUJZG`. A gate that could not see this
  could not see #550 at all.
- **Blast radius of #740's review round** (label classifier, whole-document
  scan, contact-line consumption, alias collapse): `paragraph CHANGED 1` --
  `NSUJZG` gains its work email, which is the
  `E-Mail Address:` label #740 had disclosed as unrecovered and filed as
  #730. No other document changed. Removing the `tables[:3]` and
  `paragraphs[:20]` bounds cost nothing on this corpus.
- **Rendered-output validation** (new: each output must open with
  python-docx, hold tables, and still carry the template's six PERSONAL DATA
  label rows): no false positives across 66 real renders in any arm.

Both drivers pass `original_doc_path` since #550, so the `--source-dir`
deltas above are measurements of the path a live CV render takes. (Before
that wiring the web driver reached it anyway -- `_copy_to_pipeline_input`
drops the upload where stage 6's `SAMPLE_CV_DIR` guess looks -- and the
CLI reached it only for a uid-style run from the repo root.)

## Control log (2026-11-22, `render_gate_controls.py`, #783)

The first log the script wrote rather than a person. Head was this branch,
whose `src/` equals `origin/dev` b7df3a37. Base was `ada93faa`, the merge
before #2187, which changed six stage-6 modules. Run on a laptop, four arms
side by side.

**S3-built farm.** 60 runs, picked by `build_gate_farm.py --dry-run
--local-out` (an earlier selection than the final one, which dedups by
upload hash too and swaps a few runs), each with its source docx. 20 minutes.

| control | measured | result |
|---|---|---|
| determinism | head rendered twice: paragraph CHANGED 0, fingerprint CHANGED 0 of 60; doctored twice: CHANGED 0 of 60 | PASS |
| crash guard | stale docx wiped in 4/4 arms; injected all-failed index over stale docx: refused | PASS |
| real-CV rendering | head_a 60/60 exit 0; head_b 60/60 exit 0; base 60/60 exit 0; mutated 60/60 exit 0; source docx for 60/60 | PASS |
| mutation (PERSONAL DATA work-email write removed) | paragraph CHANGED 41, fingerprint CHANGED 41; predicted 41 (rendered work email); exact match | PASS |

Base vs head: render paragraph CHANGED 0, fingerprint CHANGED 0 of 60;
doctor CHANGED 0 of 60.

**Local 66-CV farm** (`src/unified_pipeline/outputs/`, sources from
`data/sample_cvs/word/`, whose uids carry owners' names). 15 minutes. All four
controls PASS: determinism 0/0/0 of 66, stale docx wiped in 4/4 arms and the
injected index refused, 66/66 in every arm with a source docx for 65, and
mutation CHANGED 31/31 against 31 predicted. Base vs head: 0 render and 0
doctor changes of 66.

So #2187's stage-6 fixes change no CV in either farm. The mutation control
shows the gate could have seen a change if there had been one.

**Local farm against an older base** (`f7e11a2c`, dev on 2026-11-12, ten days
of stage-6 and doctor merges behind head). Controls as above, all PASS. Base
vs head: render paragraph CHANGED 24, fingerprint CHANGED 66 of 66; doctor
CHANGED 66 of 66. The per-run table listed each run by its masked uid,
and no name from the farm's name-bearing uids appeared in it. (That was the first masking rule, which kept a uid's
leading token. It kept name-shaped tokens too, so it was replaced by the
manifest allowlist above, under which every uid of this farm is hashed.)
66 of the doctor deltas are `model_review`, a lint added
in those ten days, which reports itself on every run. The next largest is
`section_consistency` (35 gained), not traced here. None of it is noise:
determinism was 0.

## Running it on a PR (#2176)

There is no GitHub-triggered job. Someone with AWS credentials in the CViche
account runs `scripts/corpus_gate_remote.sh` from their own machine, and it
starts one CodeBuild build that runs `render_gate_controls.py` against the
gate farm.

Why not a CI job: the repository is public and has 42 organization members
(as of 2026-11-22). Any PR's own copy of `ci.yml` can target a
CodeBuild-hosted Actions runner, and the webhook's `ACTOR_ACCOUNT_ID` filter
matches the event's sender, not the PR's author. So nothing on the GitHub
side decides whose code would run next to the farm. Here an AWS principal
has to start every build.

```bash
scripts/corpus_gate_remote.sh <PR number>                 # asks you to type the PR number
scripts/corpus_gate_remote.sh <PR number> --i-read-the-diff --comment
```

The script:

1. Resolves the PR's head SHA and its merge base with `gh` (the compare
   API's `merge_base_commit`), and prints both and the changed files.
2. Stops unless you type the PR number at the prompt, or pass
   `--i-read-the-diff`. Either one says you have read the whole diff at
   the printed head SHA.
3. Runs `aws codebuild start-build --source-version <head sha>`, passing
   `HEAD_SHA`, `BASE_SHA` and `PR_NUMBER` as environment variable
   overrides. It sends no buildspec override: the buildspec is the
   project's (step 4 of the setup below), not the repository's.
4. Waits for the build, reads the one line the buildspec logs to CloudWatch,
   `CORPUS_GATE_SUMMARY <base64>`, and prints the decoded report: the masked
   table `render_gate_controls.py` wrote to `--summary`. It prints the
   script's exit code too: 0 means same as base, 1 means head differs, 2
   means no evidence.
5. With `--comment`, posts that report on the PR (`gh pr comment`).

`CORPUS_GATE_PROJECT` names the project when it is not `cviche-corpus-gate`.

Run it on a PR that touches `src/unified_pipeline/stage6/`,
`stage_6_word_template.py`, `src/unified_pipeline/doctor/`,
`run_doctor.py`, `key_files/` (the template docx stage 6 renders into), or
the gate's own scripts. It is report-only. Whether it should block a merge is
a later decision.

What the build runs: CodeBuild checks out the head SHA the script printed,
not the branch, so a push made after you read the diff is not what runs, and
the buildspec stops unless the checkout is `HEAD_SHA`. It fetches `dev` and
checks that `BASE_SHA` is on it. It checks out the merge base as a second
worktree, installs the merge base's `requirements.txt`,
syncs the farm, and runs the **merge base's** `render_gate_controls.py`
with `--base` set to the merge base and `--head` to the PR's checkout. So
the controls, `render_gate.py`, `doctor_gate.py`, the comparisons and the
uid masking are all code already merged to `dev`. The PR's code runs only as
the head arm, its `src/` and `key_files/`. A PR that changes the gate's own
scripts is still measured with `dev`'s copy of them, and its changes to those
scripts are checked by `scripts/test_render_doctor_gates.py`, not by this
run.

**Trust boundary.** Only someone whose AWS credentials allow
`codebuild:StartBuild` on the project can run the gate. That IAM permission
is the whole gate: nothing on GitHub can start a build. The build runs the
PR's code with the farm on local disk and a role that can read it. That
means about 60 real CVs, with owners' names and contact details.
`render_gate_controls.py` strips `AWS_*` variables from the arms'
environment, which also hides CodeBuild's container-credentials URI, but
that is not a sandbox: head's code can read the farm files and send them
over the network. So the operator reads the PR's whole diff first, every
time, at the head SHA the script prints, which is the commit that runs.
Anyone who can start builds can also override the buildspec or the
environment. They are trusted with the farm either way.

### The farm

About 60 stored runs, copied under their own prefix so the role never needs
`cviche/runs/`:

```
cviche/gate-farm/outputs/stage_*/<uid>_*.json   stage 1a-5d JSON, the layout render_gate.py reads
cviche/gate-farm/sources/<uid>.docx             each run's uploaded original, for --source-dir
cviche/gate-farm/manifest.json                  uids (document_uids) and taxonomy codes, no names
```

`scripts/build_gate_farm.py` builds it. It lists `cviche/runs/`, keeps runs
with a stage-4 `_fields.json` and a `.docx` upload whose uid is the run's own
id in the generated shape (six of A-Z0-9; an older id with `_` or `-` in it
is left out, since `QX_ZRV` has that shape too), collapses re-runs of one
CV to the latest (by upload hash, then by stage-4 owner name, hashed in
memory only), and picks runs by greedy set cover over the taxonomy codes in
their stage-4 entries, then fills to 60 favouring the codes the farm holds
fewest of. Copies are server side. The manifest's `document_uids` list is
the allowlist `render_gate_controls.py --manifest` prints uids from.

```bash
# Read-only: print the selection (6-character uids and code coverage).
PYTHONPATH=src python3 scripts/build_gate_farm.py --dry-run
# Optionally pull the same selection to disk and run the controls on it locally.
PYTHONPATH=src python3 scripts/build_gate_farm.py --dry-run --local-out <dir>
# Write the farm (needs write access to cviche/gate-farm/). --replace rebuilds it.
PYTHONPATH=src python3 scripts/build_gate_farm.py --write
```

Rebuild it when stage 4 or the stored artifacts change shape. A rebuild
changes what every later gate run measures, so note it in the PR that
follows.

### AWS setup (Paul)

1. **Build the farm** with `--write`, above.

2. **Find the bucket's KMS key.** The bucket encrypts with SSE-KMS by
   default (with a bucket key, as of 2026-11-22), so reading an object needs
   `kms:Decrypt` on that key:

   ```bash
   aws s3api get-bucket-encryption --bucket wcm-cviche-storage \
     --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.KMSMasterKeyID'
   ```

3. **Create the role** the CodeBuild project runs as, say
   `cviche-corpus-gate-codebuild`. It can read the farm prefix, decrypt
   through S3, and write its own build log, which is where the report
   comes back. It gets no `cviche/runs/`, no Bedrock, no database, and no S3
   writes. Trust policy:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {"Service": "codebuild.amazonaws.com"},
         "Action": "sts:AssumeRole"
       }
     ]
   }
   ```

   Permissions policy. Replace `<account-id>` and `<key-id>` (from step 2);
   `cviche-corpus-gate` is the project name from step 4:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ListGateFarmOnly",
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::wcm-cviche-storage",
         "Condition": {"StringLike": {"s3:prefix": ["cviche/gate-farm/", "cviche/gate-farm/*"]}}
       },
       {
         "Sid": "ReadGateFarm",
         "Effect": "Allow",
         "Action": "s3:GetObject",
         "Resource": "arn:aws:s3:::wcm-cviche-storage/cviche/gate-farm/*"
       },
       {
         "Sid": "DecryptViaS3",
         "Effect": "Allow",
         "Action": "kms:Decrypt",
         "Resource": "arn:aws:kms:us-east-1:<account-id>:key/<key-id>",
         "Condition": {"StringEquals": {"kms:ViaService": "s3.us-east-1.amazonaws.com"}}
       },
       {
         "Sid": "OwnBuildLog",
         "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": [
           "arn:aws:logs:us-east-1:<account-id>:log-group:/aws/codebuild/cviche-corpus-gate",
           "arn:aws:logs:us-east-1:<account-id>:log-group:/aws/codebuild/cviche-corpus-gate:*"
         ]
       }
     ]
   }
   ```

   With a bucket key, the KMS encryption context is the bucket, not the
   object, so the key grant cannot itself be narrowed to the prefix; the S3
   statements are what confine the role to `cviche/gate-farm/`.

4. **Create the CodeBuild project, with no webhook.** Source GitHub, the
   public repository URL, no connection or token needed to clone it, and a
   full clone (depth 0): the build needs the merge base and `dev`'s
   history. No artifacts. The build image is `python:3.14-slim`, from ECR
   Public, the same base as the app's image. Compute is
   `BUILD_GENERAL1_MEDIUM` (4 vCPU; the script runs four renders side by
   side). Not privileged, no VPC, 60-minute timeout, logs to CloudWatch.
   The buildspec is part of the project definition below, so a PR cannot
   change it. Save this as `corpus-gate-project.yaml`, replace
   `<account-id>`, and create it:

   ```yaml
   name: cviche-corpus-gate
   description: Render/doctor corpus gate (#2176). Started by hand with scripts/corpus_gate_remote.sh. No webhook.
   source:
     type: GITHUB
     location: https://github.com/wcmc-its/CViche.git
     gitCloneDepth: 0
     reportBuildStatus: false
     buildspec: |
       version: 0.2
       env:
         shell: bash
         variables:
           DEBIAN_FRONTEND: noninteractive
           GATE_FARM: s3://wcm-cviche-storage/cviche/gate-farm/
           REPO_URL: https://github.com/wcmc-its/CViche.git
       phases:
         install:
           commands:
             - apt-get update -qq && apt-get install -y -qq --no-install-recommends git > /dev/null
             - pip install --quiet --root-user-action=ignore awscli
         pre_build:
           commands:
             - git config --global --add safe.directory '*'
             # The checkout must be the head the operator read.
             - test -n "$HEAD_SHA" && test "$(git rev-parse HEAD)" = "$HEAD_SHA"
             # The merge base must already be on dev: it supplies the base arm and every tool below.
             - git fetch --quiet "$REPO_URL" dev
             - git merge-base --is-ancestor "$BASE_SHA" FETCH_HEAD
             - git worktree add --detach /tmp/base "$BASE_SHA"
             - pip install --quiet --root-user-action=ignore -r /tmp/base/requirements.txt
             - aws s3 sync "$GATE_FARM" /tmp/gate-farm --only-show-errors --no-progress
         build:
           commands:
             # The merge base's script, with the PR's checkout only as the head arm.
             # Report-only: this step never fails the build. stdout is dropped; the
             # same masked report is in --summary, logged as one base64 line.
             - |
               echo "PR #$PR_NUMBER: base ${BASE_SHA:0:8}, head ${HEAD_SHA:0:8}"
               rc=0
               python3 /tmp/base/scripts/render_gate_controls.py /tmp/gate-farm/outputs \
                 --source-dir /tmp/gate-farm/sources --manifest /tmp/gate-farm/manifest.json \
                 --base /tmp/base --head "$CODEBUILD_SRC_DIR" --work /tmp/gate-work \
                 --summary /tmp/gate-summary.md \
                 --base-label "${BASE_SHA:0:8}" --head-label "${HEAD_SHA:0:8}" > /dev/null || rc=$?
               echo "CORPUS_GATE_EXIT $rc"
               echo "CORPUS_GATE_SUMMARY $(base64 -w0 /tmp/gate-summary.md 2> /dev/null)"
   artifacts:
     type: NO_ARTIFACTS
   environment:
     type: LINUX_CONTAINER
     image: public.ecr.aws/docker/library/python:3.14-slim
     computeType: BUILD_GENERAL1_MEDIUM
     privilegedMode: false
   serviceRole: arn:aws:iam::<account-id>:role/cviche-corpus-gate-codebuild
   timeoutInMinutes: 60
   logsConfig:
     cloudWatchLogs:
       status: ENABLED
   ```

   ```bash
   aws codebuild create-project --cli-input-yaml file://corpus-gate-project.yaml
   ```

   The render and doctor logs, which name full uids, stay under `/tmp/gate-work`
   in the build container and go nowhere. The build log can still name a
   farm key if `aws s3 sync` reports an error, so it is for AWS principals
   only; the script prints only the `CORPUS_GATE_SUMMARY` line from it.

   If CodeBuild refuses the GitHub source for want of credentials, connect
   it read-only: a CodeConnections GitHub App connection limited to this
   repository, then `codeconnections:GetConnection` and
   `codeconnections:GetConnectionToken` on that connection's ARN in the
   role. A connection does not create a webhook; the check below still
   applies.

   **Never add a webhook or a runner configuration.** Do not run `aws
   codebuild create-webhook`. Do not tick "Rebuild every time a code change
   is pushed" in the console. Do not make this, or any project that can read
   the farm, a GitHub Actions runner project. No workflow in the repository
   may name it in a `codebuild-...` `runs-on` label. Check that there is no
   webhook after creating the project, and again after any change to it:

   ```bash
   aws codebuild batch-get-projects --names cviche-corpus-gate --query 'projects[0].webhook'
   # null
   ```

5. **Decide who can start it.** `codebuild:StartBuild` and
   `codebuild:BatchGetBuilds` on the project, plus `logs:GetLogEvents` on its
   log group, is what `scripts/corpus_gate_remote.sh` needs. Whoever holds
   `StartBuild` can run any code next to the farm, so grant it only to people
   trusted with the farm's CVs.

6. **First manual run**, on a PR that touches stage 6 or the doctor, after
   reading its diff. The build runs the merge base's
   `render_gate_controls.py`, so the PR has to be based on a `dev` that
   already has it (#2190 merged):

   ```bash
   scripts/corpus_gate_remote.sh <PR number>
   ```

   The report should show all four controls `PASS` over the farm. The
   install and pre_build commands were run by hand in
   `python:3.14-slim` (2026-11-22, without the S3 sync): git and the AWS CLI
   install, the head and merge-base checks pass, and the requirements
   install. Nobody has run them in CodeBuild yet, so this run is also the
   check that CodeBuild accepts a commit SHA as the source version (not yet
   tried with a fork PR's head). To turn the gate off, delete the project
   (`aws codebuild delete-project --name cviche-corpus-gate`) or take
   `StartBuild` away.

## Known gaps, not fixed here

- **A stage-6 catch-all hides a failure from the render side of the gate.**
  Stage 6 has its own `except Exception` paths (14 in `stage6/` and
  `stage_6_word_template.py` as of 2026-11-22). One that fires renders a
  docx without the part it guarded, exits 0 and records no error, so the
  real-CV control passes and the comparison reads it as an ordinary
  difference, or as none when it fires in both arms. The doctor side is
  covered: a lint that crashed or could not read its input fails the real-CV
  control.

- **`lint_pipeline_errors` untested by either gate.** It never fires on a
  farm with no failed runs; needs a synthetic failed-run fixture, which
  belongs in `run_doctor`'s own test suite
  (`src/unified_pipeline/tests/test_run_doctor.py`), not a corpus gate.
- **`table_shape` (and any lint gated on `stage_6_docx`) untested by
  `doctor_gate.py` on a JSON-only farm.** `render_gate_controls.py` closes
  this for its own runs: it links each arm's fresh render in as
  `stage_6_wcm_documents/`.
- **No 100+ CV run yet.** This formalizes the instrument; running it at the
  scale wave 2's 24 issues will need is a separate, larger effort (real
  corpus-run cost is a standing open item, see
  `docs/analysis/HANDOFF-backlog-strategy-2026-08-08.md`).
- **The LLM success path is structurally untested by this gate, not just
  "not run this time."** `call_llm` monkeypatched to raise means both stage-6
  call sites' `except` branches fire on every single CV, every single run --
  never the branch where the call actually succeeds. A prompt or model
  change can pass this gate at 100% identical with the LLM behaviour it's
  supposedly gating not exercised at all (CODING_STANDARDS.md §5.11, §6.8).
  This gate proves determinism and formatting correctness under the LLM's
  *absence*; it says nothing about the correctness of the LLM's *output*.
