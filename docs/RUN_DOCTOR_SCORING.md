# Run doctor: how the verdict and the score are computed

Two different things get called "the score". They live in different modules and
answer different questions.

| | `run_doctor.py` | `quality_score.py` |
|---|---|---|
| Output | findings + `worst_severity` (ERROR/WARN/INFO) | integer 0-100 + band |
| Input | stage 1a/2/3b/4/5/6 JSON + source docx + output docx | `*_entries/_classified/_fields.json` + output docx |
| Number? | **no number at all** | yes |
| Where it runs | orchestrator post-run, `stage_7_doctor/<uid>_doctor.json` | `quality_score_service`, cached at `runs/{id}/quality_score.json` |

The doctor does not compute a 0-100 score. It reports *two* of the quality
score's hard-fail gates so the two cannot drift apart — that is the whole
overlap.

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

The two hard-fail lints are ERROR by construction — each one on its own caps
the quality score into the RED band.

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

### The two hard-fail caps

A cap is a ceiling on the final score, applied after the weighted sum:

- **cap 25** — `cv_owner_name_missing()`: no usable `full_name`, and not both
  `first_name` and `last_name`. The document cannot be delivered under anyone's
  name. Also trips when `*_fields.json` is absent entirely.
- **cap 40** — `FATAL_ERROR_PATTERN` matches any non-null `error` field anywhere
  in the scanned JSON: `name 'x' is not defined`, `Traceback (most recent call
  last)`, `NameError:`, `UnboundLocalError:`, `KeyError:`. Means a stage broke,
  not that a lookup came back empty.

Both predicates live in `quality_score.py` and are imported by `run_doctor.py`
for its `owner_contact_missing` / `pipeline_errors_present` lints, so the doctor
reports the gate rather than a second definition of it. What that buys is that they cannot drift apart — **not** independent
confirmation that the gate is calibrated.

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
