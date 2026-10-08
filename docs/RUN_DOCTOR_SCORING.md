# Run doctor: how the verdict and the score are computed

Two different things get called "the score". They live in different modules and
answer different questions.

| | `run_doctor.py` | `quality_score.py` |
|---|---|---|
| Output | findings + `worst_severity` (ERROR/WARN/INFO) | integer 0-100 + band |
| Input | stage 1a/2/3b/4/5/6 JSON + source docx + output docx | `*_entries/_classified/_fields.json` + output docx |
| Number? | **no number at all** | yes |
| Where it runs | orchestrator post-run, `stage_7_doctor/<uid>_doctor.json` | `quality_score_service`, cached at `runs/{id}/quality_score.json` |

The doctor does not compute a 0-100 score. It reports all five of the quality
score's hard-fail gates, each as an ERROR lint that calls the scorer's own
predicate, so the two cannot drift apart — that is the whole overlap. A sixth
cap, stage 4's failed extraction groups (below), stops short of RED and so is a
WARN lint on the same shared predicate. A call the content-filter fallback
served is a WARN lint too, but caps nothing (#1174, below). Three more caps (#822, below) cover
source content lost before the document was written; they also stop one point
under GREEN, and each calls the signal behind an existing doctor lint
(`under_extraction`, `segmentation`, `table_lost`).

## Goals

The doctor and the score exist for the person reviewing a converted CV. They should say where the document is wrong, so the reviewer doesn't have to re-read the whole CV against the source. They also tell the team what to fix next. Each goal below names how it is measured, the current baseline, and a target.

- Baselines come from batch YUYVIG (2026-10-08, dev-259 `d1e49e39`, 37 runs: 22 native PDFs and 15 docx). That batch is the first labelled set none of the lints was written from, so its numbers are held-out.
- Targets are proposals until confirmed.
- The in-sample numbers in `src/unified_pipeline/doctor/PRECISION.md` are higher, because most lints were written from the runs they are scored on.

| # | Goal | Measured by | Baseline (YUYVIG, held-out) | Target |
|---|---|---|---|---|
| 1 | **Catch what matters.** Every serious defect is flagged, on the entry where it occurs. | Share of verified HIGH defects that a finding names, by entry | 4 of 53 fully caught, 6 partly; 121 of 444 defects at any severity (27%) | Half of HIGH |
| 2 | **Don't waste the reviewer's time.** What the reviewer is shown is right. | Precision of findings shown to users (WARN and above, and Word comments in the review copy) | 279 of 397 WARN (70%); about 67 of 401 review-copy comments come from two lints that are mostly false positives (#1585) | 90% of what is shown |
| 3 | **A quiet doctor means something.** No finding never reads as "checked and fine" when the doctor couldn't check. | Every run has a doctor outcome, and every run lists what the doctor can't see | IXJMKS scored GREEN 97 with no doctor report (#1593). Runs with nothing to flag get no review copy, and no "not checked" list exists (#1589) | Every run |
| 4 | **GREEN means ship.** The score predicts the cleanup a run needs. | Share of GREEN runs carrying a verified HIGH; fit of the score to the review form's correction-time answers | 22 of 31 GREEN runs carry a verified HIGH, including all 6 runs at 100 (#822) | Under 1 in 10 GREEN runs with a HIGH |
| 5 | **Point to the fix.** A finding sits where the problem is and shows what's wrong. | Findings anchored to the document text, quoting the source text at stake; certain fixes applied or suggested as tracked changes | Comments are anchored (#1543) but don't quote the source; no fix is applied or suggested (#1591) | Every shown finding quotes its source |
| 6 | **Measured, not asserted.** Every lint's precision and recall are known. | `PRECISION.md` has a held-out row for every lint that fires; labels grow from each batch autopsy and from reviewer verdicts | In-sample only (62 runs) until #1586; no reviewer verdicts (#1587) | Every lint, held-out |
| 7 | **Feed the pipeline.** Findings rank pipeline fixes by the cleanup they cause. | Each finding names the stage that caused it; a corpus Pareto by cause | Stage named in autopsies only, not in findings | Every finding names its stage |

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

Each dimension returns a penalty fraction in `[0, 1]`. Weighted, normalized
against total weight (100), so a fully-penalized run scores 0 and a clean run
scores 100:

```python
raw   = 100 * (1 - sum(weight_i * fraction_i) / 100)
final = min(raw, *hard_fail_caps)     # caps only ever lower it
score = round(max(0, final))
```

### Dimensions and weights

| Weight | Dimension | Penalty fraction |
|---:|---|---|
| 25 | Pipeline/API errors **(HARD-FAIL, cap 40)** | 1.0 on a fatal pattern; else `min(1, nonnull_errors / 3)` |
| 15 | CV owner name/contact **(HARD-FAIL, cap 25)** | 1.0 if name missing or no `*_fields.json`; else 0.4 no location inference + 0.3 no primary location + 0.3 no contact field |
| 15 | T-bucket share (3b catch-all) | 0 at ratio ≤0.03, linear to 0.4 at 0.08, to 0.8 at 0.15, 1.0 above; +0.2 if `t_validation.error` |
| 12 | Sparse tables in output docx | `0.6*(sparse_table_ratio/0.25) + 0.4*((global_empty_ratio-0.10)/0.40)`, over tables carrying CV content only: a table whose every non-empty cell is template text is skipped, and 0 if none remain (#452) |
| 10 | Duplicate-entry ratio | 0 at ≤0.10, linear to 0.4 at 0.30, to 0.8 at 0.50, 1.0 above; +0.1 if entries coverage >130% |
| 10 | Raw-tab / prompt-echo artifacts | `0.6*(raw_tab_paragraphs/20) + 0.4*(echo_paragraphs/15)` |
| 13 | Field-extraction sparseness | `0.5*((allnull_or_zerocov/total)/0.10) + 0.5*((1-success_rate)/0.10)` |

All fractions clamp to `[0, 1]`. A dimension whose artifact is missing scores
1.0 (full penalty); a missing docx scores 0.5.

### The five hard-fail caps

A cap is a ceiling on the final score, applied after the weighted sum. When
several fire, the lowest wins. Only the first two (owner, fatal error) also
carry weight as dimensions; the other three are weight-0 gates, so adding them
moved no clean run's raw score.

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

### The three content-loss caps (#822)

Cap-only gates like protected data (`CAP_ONLY_GATES`, weight 0, so no run's raw
score moves), but soft: each caps a run at `CONTENT_LOSS_CAP` (84, one point
under GREEN), so a run that lost source content cannot read "ship" and is not
pushed toward RED. No weighted dimension measures lost content; these call the
doctor's own signals, restricted to the ones batch IPXFBA hand-checked as real.

- `score_under_extracted_records()` — the doctor's `under_extraction` lint, any
  finding (in IPXFBA all 4 findings were true positives, but only 2 lost
  records outright; outside IPXFBA a finding can fire with nothing lost).
- `score_fused_entries()` — `mega_entries` (`count_mega_entries`) at
  `MEGA_ENTRIES_CAP_MIN` (2) or more entries; one fused entry is common and
  harmless, so the threshold is a count (7 of 9 flagged entries were real).
- `score_lost_source_table()` — the primitive behind `table_lost`
  (`find_lost_blocks`), worst lost table at `LOST_TABLE_CAP_MIN_LINES` (5) or
  more lines. It reads the original uploaded `.docx` from the
  `SOURCE_DOCX_SUBDIR` (`source/`) of the scored directory; the web service
  stages `input/*.docx` there and `score_one.py --source` does the same. With no
  readable source the gate is not evaluated, so a score computed without it can
  sit above the web app's.

Both thresholds were fitted to one batch and are named in the code to be revisited.

Which cap the run page names when several sit at 84: the three content-loss caps
and the stage-4 cap share a value, and the run page's reason and doctor-lint
pointer (`run_quality_report.cap_source`) is the first matching flag, in
`CAP_ONLY_GATES` order. The order is most specific first: lost table (the loss
is measured against the delivered docx), fused entries (a count of swallowed
records), under-extraction (fires on any finding, including ones that lost
nothing), then stage 4's failed group (a call failed and was retried; no loss is
measured). The score and band do not depend on the order, only the pointer.
In batch IPXFBA it decides two runs: EKGTXD (under-extraction and fused entries)
and PBSGQZ (fused entries and a stage-4 failure) both point at the fused-entries
reason, which is where their verified loss is (EKGTXD's two under-extraction
findings lost no records).

### Bands

| Score | Band | `quality_gate` verdict |
|---|---|---|
| ≥ 85 | GREEN (ship) | PASS |
| ≥ 60 | YELLOW (human cleanup needed) | REVIEW |
| < 60 | RED (re-run / do-not-deliver) | BLOCK |

Gate modes: `off` (always passes), `advisory` (computes, never blocks —
**default**), `block` (RED fails). The gate never raises on a low score.

### Calibration caveat (read this before trusting an absolute number)

From the module docstring (2026-06-02): scored against 8 production runs, **all
scored 25-40 — every run was RED.** Two systemic causes dominated the whole
distribution: the stage 3b `name 'response' is not defined` bug (tripped the
cap-40 gate on ~100% of runs) and systemic raw-tab / prompt-echo artifacts. The
score discriminates *within* that range, but the 85/60 thresholds have not been
re-baselined since. Treat GREEN as "no detected problems", never
"human-verified correct".

## Running them

```bash
# doctor, one local run
PYTHONPATH=src python -m unified_pipeline.run_doctor <outputs_root> <uid> \
    [--source cv.docx] [--out report.json]
PYTHONPATH=src python3 scripts/doctor_one.py <outputs_root> <uid>   # one-line TSV

# doctor, across a corpus, aggregated by lint
PYTHONPATH=src python3 scripts/corpus_doctor_sweep.py <corpus_dir>

# score
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
