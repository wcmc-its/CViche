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

**doctor_gate / doctor_gate_compare:**
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

## Known gaps, not fixed here

- **`lint_pipeline_errors` untested by either gate.** It never fires on a
  farm with no failed runs; needs a synthetic failed-run fixture, which
  belongs in `run_doctor`'s own test suite
  (`src/unified_pipeline/tests/test_run_doctor.py`), not a corpus gate.
- **`table_shape` (and any lint gated on `stage_6_docx`) untested here.**
  Needs a farm with `render_gate.py`'s own output symlinked back in as
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
