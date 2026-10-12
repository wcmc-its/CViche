# Run doctor: how the verdict and the score are computed

Two different things get called "the score". They live in different modules and
answer different questions.

| | `run_doctor.py` | `quality_score.py` |
|---|---|---|
| Output | findings + `worst_severity` (ERROR/WARN/INFO) | integer 0-100 + band |
| Input | stage 1a/2/3b/4/5/6 JSON + source docx + output docx | the doctor's report + `*_entries/_classified/_fields.json` + output docx |
| Number? | **no number at all** | yes |
| Where it runs | orchestrator post-run, `stage_7_doctor/<uid>_doctor.json` | `quality_score_service`, cached at `runs/{id}/quality_score.json` |

The doctor does not compute a 0-100 score, but since #1595 the score is built
from the doctor's report: its WARN and ERROR findings, weighted by each lint's
measured precision and typical fix minutes, are the score's one weighted
dimension, and a run with no report cannot be GREEN. The doctor also reports
all five of the score's hard-fail gates, each as an ERROR lint that calls the
scorer's own predicate, so the two cannot drift apart. A sixth cap, stage 4's
failed extraction groups (below), stops short of RED and so is a WARN lint on
the same shared predicate. A call the content-filter fallback served is a WARN
lint too, but caps nothing (#1174, below).

## Goals

The doctor and the score exist for the person reviewing a converted CV. They should say where the document is wrong, so the reviewer doesn't have to re-read the whole CV against the source. They also tell the team what to fix next. Each goal below names how it is measured, the current baseline, and a target.

- Baselines come from batch YUYVIG (2026-10-08, dev-259 `d1e49e39`, 37 runs: 22 native PDFs and 15 docx). That batch is the first labelled set none of the lints was written from, so its numbers are held-out.
- Paul approved the targets of goals 1, 2 and 4 on 2026-10-08, unchanged. The other targets are proposals until confirmed.
- **Current** is the held-out measurement YUY-HO in `PRECISION.md` (origin/dev `a7ca2f66`, the same 37 runs), where it measured that goal.
- The in-sample numbers in `src/unified_pipeline/doctor/PRECISION.md` are higher, because most lints were written from the runs they are scored on.

| # | Goal | Measured by | Baseline (YUYVIG, held-out) | Target | Current (YUY-HO) |
|---|---|---|---|---|---|
| 1 | **Catch what matters.** Every serious defect is flagged, on the entry where it occurs. | Share of verified HIGH defects that a finding names, by entry | 4 of 53 fully caught, 6 partly; 121 of 444 defects at any severity (27%) | Half of HIGH (approved 2026-10-08, Paul) | 19 of 53 HIGH caught; 13 of them out of sample, the other 6 from lints written from these runs |
| 2 | **Don't waste the reviewer's time.** What the reviewer is shown is right. | Precision of findings shown to users (WARN and above, and Word comments in the review copy) | 279 of 397 WARN (70%); about 67 of 401 review-copy comments come from two lints that are mostly false positives (#1585) | 90% of what is shown (approved 2026-10-08, Paul) | 278 of 396 WARN true (70%) on the dev-259 doctor; about 282 of 338 (83%) on the current doctor, not re-judged and partly in-sample |
| 3 | **A quiet doctor means something.** No finding never reads as "checked and fine" when the doctor couldn't check. | Every run has a doctor outcome, and every run lists what the doctor can't see | IXJMKS scored GREEN 97 with no doctor report (#1593). Runs with nothing to flag get no review copy, and no "not checked" list exists (#1589) | Every run | |
| 4 | **GREEN means ship.** The score predicts the cleanup a run needs. | Share of GREEN runs carrying a verified HIGH; fit of the score to the review form's correction-time answers | 22 of 31 GREEN runs carry a verified HIGH, including all 6 runs at 100 (#822) | Under 1 in 10 GREEN runs with a HIGH (approved 2026-10-08, Paul) | 16 of 24 GREEN runs carry a HIGH with the #1595 score (YUYVIG, whose verdicts now feed the weights); bounded by goal 1, see "Measured (#1595)" |
| 5 | **Point to the fix.** A finding sits where the problem is and shows what's wrong. | Findings anchored to the document text, quoting the source text at stake; certain fixes applied or suggested as tracked changes | Comments are anchored (#1543) but don't quote the source; no fix is applied or suggested (#1591) | Every shown finding quotes its source | |
| 6 | **Measured, not asserted.** Every lint's precision and recall are known. | `PRECISION.md` has a held-out row for every lint that fires; labels grow from each batch autopsy and from reviewer verdicts | In-sample only (62 runs) until #1586; no reviewer verdicts (#1587) | Every lint, held-out | |
| 7 | **Feed the pipeline.** Findings rank pipeline fixes by the cleanup they cause. | Each finding names the stage that caused it; a corpus Pareto by cause | Stage named in autopsies only, not in findings | Every finding names its stage | |

What the doctor is not:

- **It is deterministic.** No LLM calls and no network. A sampled LLM audit that estimates the doctor's miss rate is a separate tool (#1592).
- **It never fails a run.** A doctor problem is logged and the run stands (see "Running them"). Goal 3 asks only that a run without a doctor report be marked "not checked".
- **It is not the fix.** When a lint is near 100% precise and fires on a large share of runs, the bug belongs upstream. `role_consistency` `owner_pi_role_empty` is an example (#1403). Fix the pipeline, then retire the lint from the user view (#1586, #1589).

## Part 1: the doctor's verdict

`src/unified_pipeline/run_doctor.py`

### Step 1 — run each lint whose artifacts loaded

16 lints, each encoding one observed production failure class (see the module
docstring for the ranked list). A missing artifact **skips** its lint with an
INFO note; an unreadable one is ERROR. No LLM calls, no network.

Two lints deliberately break the skip convention:

- `owner_contact_missing` runs even when `*_fields.json` is absent, because the
  scorer caps at 25 for an absent artifact too. Skipping it would report the
  *more* broken run more quietly (#437).
- `pipeline_errors_present` scans whatever subset of stage 2/3b/4 loaded, since
  the scorer scans whatever landed.

### Step 2 — assign a severity per finding

`WARN` means *unusual*, not *present* (#438). Three lints compare this run's
magnitude against the corpus p75 measured over 73 runs of the 2026-07-25 batch:

```python
def _magnitude_severity(observed, threshold):
    return "WARN" if observed >= threshold else "INFO"
```

| Lint | Magnitude | p75 threshold |
|---|---|---|
| `table_shape` | malformed-row ratio | 0.27 |
| `table_shape` | defects per table | 4 |
| `missed_headers` | headers missed per run | 6 |
| `classified_unrendered` | entries lost per run | 2 |

Before this, 77% of the corpus landed on the identical WARN verdict, so an
unchanged verdict was never evidence of no regression. These are static
constants and go stale as the pipeline improves — #440 replaces them with a
live corpus baseline.

The five hard-fail lints (`no_output`, `owner_contact_missing`,
`protected_data_in_output`, `pipeline_errors_present`, `stage3b_fallback_ratio`)
are ERROR by construction — each one on its own caps the quality score into
the RED band.

### Step 3 — roll up

```python
counts = {ERROR: n, WARN: n, INFO: n}
worst_severity = first non-zero of (ERROR, WARN, INFO)
```

CLI exits **1 if any ERROR or WARN**, else 0. The library entry point
`run_doctor(root, uid, source=None)` never exits and never raises on bad
artifacts.

### Step 4 — rank for display (does not affect severity)

Ordering is by **surprise**, not count. A lint that fires on 88% of runs tells
you almost nothing; one that fires on 1.4% tells you a lot.

```python
lint_surprise(lint) = log2(1 / prevalence[lint])   # bits; unknown lint -> 0.001, i.e. maximally surprising
rank_lints          = sort by (-surprise, -count, name)
```

Prevalence table (same 73 runs): `output_hygiene` 0.877, `table_shape` 0.562,
`missed_headers` / `classified_unrendered` 0.288 … `pipeline_errors_present`
0.001. Ranking by raw count got this backwards — the ubiquitous lints are also
the ones that fire many times, so they consumed half the report.

## Part 2: the 0-100 quality score

`src/unified_pipeline/quality_score.py`

Since #1595 the score is built from the doctor's findings. Each dimension
returns a penalty fraction in `[0, 1]` and costs `weight * fraction` points:

```python
raw   = 100 - sum(weight_i * fraction_i)
final = min(raw, *caps)     # caps only ever lower it
score = round(max(0, final))
```

### The doctor-findings dimension (weight 40)

`score_doctor_findings` reads the run's `<uid>_doctor.json` and estimates the
minutes of cleanup it names:

```python
minutes  = sum(precision(finding) * severity_weight * fix_minutes(lint)  for each finding)
fraction = minutes / (minutes + 5)
```

- **precision** is the finding's hand-checked TP / judged as the review copy's
  gate reads it (#1589): `doctor.precision.finding_precision` over
  `load_gate_ledger()`, so the `doctor/PRECISION.md` row of the finding's own
  shape, with YUYVIG's held-out verdicts (YUY-HO) folded in for lints whose
  code is unchanged since dev-259 (`HELD_OUT_CHANGED` lists the rest). The score
  and the gate read the same numbers (Paul, 2026-10-08). A finding with no
  verdicts gets `UNMEASURED_PRECISION_PRIOR`, 0.5.
- **severity weight** is 1 for WARN and ERROR, 0 for INFO. INFO findings are not
  shown as problems.
- **fix minutes** come from `LINT_FIX_MINUTES`, #822's strawman unit costs per
  kind of defect: protected data 5, owner contact block 2, a lost record 1, an
  entry in the wrong place 0.5, a wrong field 0.3, a duplicate or a header row
  0.2, a formatting artifact 0.1, and 0 for a finding that reports how the run
  went (a fallback model, a failed call) rather than a defect. They are asserted,
  not measured: refit them once #1587 records reviewers' correction times.
- **The 5** (`DOCTOR_HALF_WEIGHT_MINUTES`) is derived, not chosen: it makes
  `GREEN_MAX_CLEANUP_MINUTES` (3) cost exactly the 15 points between 100 and
  the GREEN line. The penalty never reaches the full 40 points, so more cleanup
  always scores lower, and estimates alone never take a run below YELLOW. RED is
  left to the hard-fail caps ("nothing below YELLOW", Paul on #822, 2026-10-02).
- **No report, no GREEN (#1593).** With no readable doctor report the dimension
  costs nothing and caps the run at `NOT_CHECKED_CAP` (84), and
  `missing_evidence` names the missing report, so `data_complete` is false.

The detail string names the five costliest lints, with their count, precision
and minutes, so each point traces to findings the reviewer can see.

### Retired weights (#1595)

The seven dimensions the score was built from until #1595 were tuned in June
2026, when every run was RED. On the two labelled batches none tracked the
verified defects:

Rank correlation of each penalty with label cost (a penalty should correlate
positively):

| Dimension | Old weight | EBYSBC/s7ab/pilot (62 runs) | YUYVIG (37 runs) |
|---|---:|---|---|
| Pipeline/API errors | 25 | 0 on every run | 0 on every run |
| CV owner name/contact | 15 | -0.16 | -0.08 |
| T-bucket share | 15 | -0.08 | -0.13 |
| Sparse tables | 12 | +0.14 | -0.08 |
| Raw-tab / prompt-echo artifacts | 10 | +0.17 | +0.12 |
| Field-extraction sparseness | 13 | +0.07 | +0.06 |
| Duplicate-entry ratio | 10 | 0 on every run | 0 on every run |

A dimension at zero on every run of a batch, or uncorrelated across two
labelled batches, earns no weight (#1595, item 8). The two that carry a
hard-fail cap (pipeline errors, owner name) stay as weight-0 gates; the other
five were deleted with their tests.

### The five hard-fail caps

A cap is a ceiling on the final score, applied after the weighted sum. When
several fire, the lowest wins. All five are weight-0 gates since #1595 (the
owner and fatal-error rows used to carry weight too).

- **cap 20** — `no_output_produced()`: no rendered docx at all. Nothing to
  deliver (#745). Doctor lint: `no_output`.
- **cap 25** — `cv_owner_name_missing()`: no usable `full_name`, and not both
  `first_name` and `last_name`. The document cannot be delivered under anyone's
  name. Also trips when `*_fields.json` is absent entirely.
- **cap 40** — `FATAL_ERROR_PATTERN` matches any non-null `error` field anywhere
  in the scanned JSON: `name 'x' is not defined`, `Traceback (most recent call
  last)`, `NameError:`, `UnboundLocalError:`, `KeyError:`. Means a stage broke,
  not that a lookup came back empty.
- **cap 25** — `score_protected_data()`: protected personal data (date of
  birth, SSN and the other #820 label/value shapes) reached the rendered docx.
  Cap-only gate in `CAP_ONLY_GATES`, not in `DIMENSIONS`. It runs the same scan
  as the doctor's `protected_data_in_output` lint (#825).
- **cap 40** — `stage3b_fallback_ratio_exceeded()`: more than
  `STAGE3B_FALLBACK_RATIO_THRESHOLD` (5%) of stage 3b's classification batches,
  or of its entries, fell back to a default code (#810). These failures are
  recorded as numbers in `meta.stats`, which the error-string scan above cannot
  see. Same cap as a fatal error, since both mean a stage produced output that
  looks real but isn't. Doctor lint: `stage3b_fallback_ratio`. The 5% is a
  starting calibration, not a fitted percentile.

Every predicate lives in `quality_score.py` (or, for protected data, in the
shared lint it calls), and the doctor's matching lint uses the same function,
so the doctor reports the gate rather than a second definition of it. What that buys is that they cannot drift apart — **not** independent
confirmation that the gate is calibrated.

### The stage-4 group-failure cap (not a hard fail)

- **cap 84** — `stage4_group_failures()`: stage 4 extracts one taxonomy group per
  LLM call, and a call that fails (an invalid reply, a timeout, a provider
  error such as a content filter) writes `extraction_error` on every entry of
  the group (#1174). The recovery pass then retries those entries on their own
  prompt; one it fills in keeps its `extraction_error` and gains
  `extraction_success=True`, so neither `stats.extraction_failed` (counted after
  recovery) nor the error-string scan above (it reads only a key named exactly
  `error`) sees a group that was rescued in full. Cap-only gate in
  `CAP_ONLY_GATES`, weight 0, so no clean run's raw score moves. The cap is
  `BAND_GREEN - 1`: a run with a failed group cannot be GREEN, and reads
  YELLOW. Doctor lint: `stage4_group_failures` (WARN, evidence names the
  taxonomy codes, the cause, and how many entries were rescued).

  What it counts: entries whose `extraction_error` is set, other than the
  per-entry "No matching extraction in LLM response" (a successful call whose
  reply omitted one entry, already counted by the sparseness dimension), plus
  `stats.failed_batches > 0`. A rescued group counts: the rescued entries were
  read on a different prompt and can carry wrong values.

  What it deliberately does not do: grade the cap by the share of entries left
  unextracted. No measurement supports a threshold, and a failed group is
  rare (3 of 163 corpus CVs), so a flat cap is the claim the evidence supports.
  A call served by the Sonnet 4.6 content-filter fallback (#1207) that
  succeeded is not a failed group, and does not cap (next section).

### A fallback-served call (no cap)

- **no cap** — `llm_fallback_served()`: when a Sonnet-5 call ends
  `content_filtered`, `llm/bedrock.py` retries it down the fallback chain
  (#1207). The retry succeeds, so nothing marks an error. The result carries
  `served_by_fallback_model`; stage 4 copies it onto the entries of the
  taxonomy group that call served (`llm_fallback_model`), and stage 4.5 lists
  its served calls under `llm_fallback_calls` in its artifact. Both are
  write-only provenance: only the doctor reads them. Doctor lint:
  `llm_fallback_served` (WARN, one finding per section: the taxonomy code, or
  the research summary call).

  Why no cap (#1174, Paul 2026-10-05): a served call reaches the run only after
  its reply parsed and validated; otherwise it would be a failed group or a
  recorded stage failure, which do cap. The cap it carried until then (84)
  marked runs whose fallback output was correct: EOAHMI run BRUSUZ scored raw
  97.85 and was capped to 84 YELLOW for a correct stage-4.5 relevance score and
  one correct stage-4 M1 entry, while a run whose research summary failed on
  every model was not capped at all.

  What it does not see: a fallback-served call in stage 1a, 2, 3a, 3b, 5b to
  5d or 6, or in stage 4's recovery, owner-name or location calls. Those stages
  record at most one observed model per run, which cannot show that a single
  call was served by the fallback. Also unseen: a call that the fallback also
  filtered, which is a failed group (above) or a recorded stage failure
  (below). The gap is tracked in the residual of #1174.

### A recorded stage failure

`lint_stage_failure_recorded` (doctor lint `stage_failure_recorded`) reports a
stage the driver recorded as failed in `stage_errors/<uid>_stage_errors.json`
(#745). `score_pipeline_errors` already reads that record for its cap-40 gate;
the doctor did not, so a run could score RED with a clean doctor. A fatal
record is an ERROR, like the gate it mirrors, and a non-fatal one a WARN. For
stage 4.5 the finding says the research summary is missing because the stage
raised, as opposed to a CV with no research content. The lint can only fire on
the CLI or batch path: on the web path a raising stage fails the run, and the
doctor runs only after a terminal success. Whether a missing summary
should fail a web run at all is a separate decision (#1174) and is not made
here.

### The retired content-loss caps (#822, #1595)

Until #1595 nine cap-only gates held a run at 84 for content the pipeline lost
or garbled: an under-extracted entry, fused entries, a lost source table, the
owner cut from citations, co-authors cut to "et al.", grant details shifted,
an application rendered as an award, header rows rendered as records, and rows
that lost their group header. On YUYVIG they decided the band almost at random:
six runs capped, all at 84, by five different caps; MVUREJ was capped for one
true MED finding with 0 HIGH while 8 uncapped runs carried 3 or more HIGH.

Each of them was a doctor lint, so each now costs its findings' precision-weighted
minutes in the doctor dimension instead of flipping the band (Paul approved the
redesign on #1595, 2026-10-08). A cap is kept only for a must-fix class with a
measured precision: protected personal data (RED). No lost-record or attribution
lint has a measured precision of 80% on 20 judged findings outside the batches
it was written from, except `multi_record_coverage`, which also fires on runs
with no verified HIGH (2 of the 14 zero-HIGH runs of the fit set), so it does not
cap either.

### Bands

| Score | Band | `quality_gate` verdict |
|---|---|---|
| ≥ 85 | GREEN (ship): about 3 minutes of estimated cleanup or less, and checked | PASS |
| ≥ 60 | YELLOW (human cleanup needed) | REVIEW |
| < 60 | RED (re-run / do-not-deliver): a hard-fail cap only | BLOCK |

Gate modes: `off` (always passes), `advisory` (computes, never blocks —
**default**), `block` (RED fails). The gate never raises on a low score.

`GREEN_MAX_CLEANUP_MINUTES = 3` was set on the fit set, batches EBYSBC, s7ab and
pilot (62 runs; labels converted from their verified autopsies). 48 of the 62
carry a verified HIGH, so GREEN can only be honest if it is rare. Runs under
3.5 estimated minutes: 5, none with a HIGH; under 4: 8, 1 with a HIGH; under 5:
14, 5 with a HIGH. The 85/60 lines themselves are unchanged.

### Measured (#1595)

`scripts/score_vs_autopsy.py`, label cost = 3 x HIGH + 1 x MED + 0.25 x LOW.
Base is origin/dev `8e29dd24` re-scored over the same artifacts.

| Batch | Doctor | Score rank r with cost, base → #1595 | GREEN with a verified HIGH, base → #1595 |
|---|---|---|---|
| EBYSBC/s7ab/pilot, 62 runs (fit set) | current | -0.35 → -0.69 | 4 of 6 → 0 of 4 |
| YUYVIG, 37 runs (held out for everything but the weights) | dev-259 as stored, gate ledger (YUY-HO folded) | -0.14 → -0.63 | 22 of 31 → 16 of 24 |
| YUYVIG, 37 runs (lints partly written from it) | current | -0.14 → -0.67 | 22 of 31 → 15 of 24 |

The first and third rows were measured with the in-sample per-lint weights,
before the score switched to the gate's ledger. On the YUYVIG held-out row the
switch moves 14 of 37 runs by 1 or 2 points, changes no band, and leaves the
rank r (-0.63), GREEN with a HIGH (16 of 24) and the caps as they were. Since
the switch, YUYVIG's own verdicts feed the precision weights, so YUYVIG is no
longer fully held out for this score: the minutes, the prior, the GREEN line
and the curve were still fixed before it was scored, but the weights were not.
The next labelled batch is the clean held-out test.

The held-out rank correlation passes #1595's bar (the doctor WARN count's
+0.59). GREEN with a HIGH does not meet goal 4 (under 1 in 10), and no GREEN
line can: three YUYVIG runs with a verified HIGH have no WARN finding at all
(0 estimated minutes), so a score built from the doctor is bounded by the
doctor's recall (goal 1). The one cap on a zero-HIGH YUYVIG run is IXJMKS's,
which had no doctor report (#1593).

### Calibration caveat (read this before trusting an absolute number)

The minutes are a ranking, not a stopwatch: the fix minutes are strawman
costs, the precision weights include YUYVIG's own verdicts, and the
GREEN line is fitted to one labelled set. Treat GREEN as "nothing the doctor
reliably flags", never "human-verified correct". Refit on each labelled batch
with `scripts/score_vs_autopsy.py`, and against correction times once #1587
collects them.

## Running them

```bash
# doctor, one local run
PYTHONPATH=src python -m unified_pipeline.run_doctor <outputs_root> <uid> \
    [--source cv.docx] [--out report.json]
PYTHONPATH=src python3 scripts/doctor_one.py <outputs_root> <uid>   # one-line TSV

# doctor, across a corpus, aggregated by lint
PYTHONPATH=src python3 scripts/corpus_doctor_sweep.py <corpus_dir>

# score (the directory must hold the run's <uid>_doctor.json, or the run reads
# as not checked and is capped at 84)
python3 src/unified_pipeline/quality_score.py <run_output_dir> [run_id]
python3 src/unified_pipeline/quality_score.py <run_output_dir> --gate   # exit 1 if RED
```

In the web path the orchestrator computes both after a successful run, then
passes score + doctor into the Teams card (`notifications.build_teams_payload`).
Doctor failures are caught and logged — they can never fail the run. The scorer
only finds artifacts when the storage backend is S3; in pure-local mode there is
nothing to score from.

## Known blind spots

The doctor's `CHANGED 0` across a corpus is not blanket proof of no regression:
it is blind to source-docx lints and to `pipeline_errors`, and a stray output
filename can fake a 100%-changed result. See
`docs/analysis/HANDOFF-run-doctor-improvements-2026-07-25.md`.
