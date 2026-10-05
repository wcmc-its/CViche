# Doctor lint precision ledger

One row per lint: how often its findings were real the last time anyone measured, on how many, and how many verified defects it caught. Asked for in #819: before this ledger, each batch's hand-check lived in an issue comment, and the next batch measured the same ratio again from scratch.

The rule is #819's: a hand-check that does not update this file did not happen. A lint PR re-measures its own row with the commands below and adds a measurement id. A hand-check appends rows to the history table at the end.

## How to read a row

- **hits**: the lint's findings over the scored runs (status `ran`). **warn+** is how many of those were WARN or ERROR.
- **judged TP / partial / FP**: the verdicts an adversarial verifier gave the lint's findings while auditing the batch. **TP / judged** is the hand-checked precision. Only the EBYSBC batch has verdicts.
- **matched / hits**: hits that name the same entry (`element_idx_start`) as a verified autopsy finding on the same run. This is a lower bound on precision: the autopsy recorded the defects it verified, not every true signal, so an unmatched hit still needs a hand-check before it counts as a false positive. It also counts a hit on the right entry for the wrong reason as a match. "names text" means the lint's findings quote text and name no entry, so they cannot be matched by index.
- **caught**: the verified findings this lint matched, which is its contribution to recall.
- **Evidence cap.** The doctor prints at most 3 evidence items per finding: the stage-6 re-emit in `doctor/lints/render.py` slices to 3, and `offschema_fields` keeps `FIELD_EVIDENCE_MAX_VALUES = 3` (`doctor/lints/extraction.py`). The scorer reads only the indices a finding prints. A finding about more entries is matched on the 3 it lists, so on the reroute rows and `offschema_fields`, matched and caught can undercount, and they depend on which 3 entries come first. On M1 the cap hides 85 of 206 reroute indices (in 11 of 72 findings) and 19 of 89 `offschema_fields` indices (in 5 of 47 findings). M1's doctor was re-run once with both caps lifted, in a scratch copy that is not committed: no matched, caught or recall number changed. Only the reroute rows' unmatched-index lists grew, `reroute_refused` from 57 to 92 indices and `reroute_same_family` from 16 to 46. Another arm can differ, so a lint PR on one of these rows says how many of its findings hit the cap.
- `stage6_render_warnings` is one lint key that re-emits about twenty unrelated stage-6 checks, so it has one row per message shape. The shapes are defined in `scripts/doctor_vs_autopsy.py` (`_STAGE6_SHAPES`).

## Who reads this file

`doctor/precision.py` parses the per-lint table below at run time, so keep its shape: the first table under the "Per-lint precision" heading, a `lint` column of backticked keys (a `stage6_render_warnings` shape row adds `: ` and the backticked shape), and a `TP / judged` column of `<tp> / <judged>` or `none`. Columns are found by header name. Shape rows are pooled into their lint key, because a finding carries the key. A lint listed twice keeps its first row. What it feeds:

- Each run's doctor report gets a `lint_precision` block, and the top lints in `doctor.tsv` and the lint on the Teams card's Doctor line carry `p~0.50 n=8` (TP / judged, and judged), or `p unmeasured`.
- #813's remediation gate, `precision.remediation_allowed`: a lint may drive a section retry only if it is one of #813's LLM-judgement lints (`missed_headers`, `segmentation`, `under_extraction`) with a TP / judged of at least 80% on at least 20 judged findings. On M1, none qualifies.

## Measurements

| id | date | doctor code | runs scored | labels | issue |
|---|---|---|---|---|---|
| M1 | 2026-10-02 | origin/dev `c3d87c5f`, over the base render of the same SHA | 62 of the 63-run farm: EBYSBC 40, s7ab 10, pilot 12 (the 13th pilot run has no verified autopsy) | 516 verified findings (EBYSBC 312, s7ab 112, pilot 92), 487 of them carrying an entry index; 186 EBYSBC verdicts | #819 |
| M3 | 2026-10-04 | origin/dev `fb466a0f`, over the base render of the same SHA (`doctor_gate.py`: 63 runs, 1,275 findings) | 62 of 63, as M1 | as M1 | #822 |
| M2-junk | 2026-10-04 | origin/dev `fb466a0f` plus the `junk_or_header_row` lint, over the base render of `fb466a0f` | 62 of the 63-run farm, as M1 | as M1 | #985, #986, #1222 |
| M3b-5c | 2026-10-04 | `feat/ebysbc-doctor-5c-and-contact` on origin/dev `fb466a0f`, over the base render of `fb466a0f` | as M1 | as M1 | #1345, #1222 |
| M2-enrich | 2026-10-04 | origin/dev `fb466a0f` plus `pubmed_title_truncated` and `enrichment_pubtype_mismatch`, over the base render of `fb466a0f` and the farm's stored stage-5 JSON | 62 of the 63-run farm, as M1 | as M1 | #1358 (EBYSBC E19) |

The EBYSBC verdicts were given on the doctor deployed for that batch (dev-242, `f4f087fc`). On M1's code every judged (run, lint) pair still fires, so the verdict columns describe the same findings as the hit columns.

## Per-lint precision

| lint | hits | warn+ | judged TP / partial / FP | TP / judged | matched / hits | caught | measured |
|---|---|---|---|---|---|---|---|
| `classified_unrendered` | 1 | 0 | 0 / 0 / 1 | 0 / 1 (0%) | names text | 0 | M1 |
| `contact_slot_lost` | 1 | 1 | 1 / 0 / 0 | 1 / 1 (100%) | 1 / 1 (100%) | 1 | M3b-5c |
| `date_only_lines` | 2 | 0 | none | none | names text | 0 | M1 |
| `dedup_drops` | 12 | 10 | 2 / 2 / 4 | 2 / 8 (25%) | names text | 0 | M1 |
| `duplicate_passages` | 1 | 1 | none | none | names text | 0 | M1 |
| `duplicate_records` | 8 | 8 | 4 / 0 / 1 | 4 / 5 (80%) | names text | 0 | M1 |
| `enrichment_failures` | 15 | 15 | 3 / 4 / 0 | 3 / 7 (43%) | names text | 0 | M1 |
| `enrichment_pubtype_mismatch` | 2 | 2 | 2 / 0 / 0 | 2 / 2 (100%) | 1 / 2 (50%) | 1 | M2-enrich |
| `implausible_year` | 21 | 21 | 1 / 0 / 1 | 1 / 2 (50%) | 7 / 21 (33%) | 5 | M1 |
| `junk_or_header_row` | 104 | 104 | 102 / 2 / 0 | 102 / 104 (98%) | 65 / 104 (63%) | 20 | M2-junk |
| `llm_fallback_served` | 1 | 1 | 1 / 0 / 0 | 1 / 1 (100%) | names text | 0 | M1 |
| `missed_headers` | 21 | 0 | 4 / 2 / 2 | 4 / 8 (50%) | names text | 0 | M1 |
| `offschema_fields` | 47 | 14 | 16 / 2 / 5 | 16 / 23 (70%) | 34 / 47 (72%) | 28 | M1 |
| `output_hygiene` | 55 | 0 | 30 / 3 / 0 | 30 / 33 (91%) | names text | 0 | M1 |
| `pipe_leaks` | 5 | 5 | 0 / 0 / 2 | 0 / 2 (0%) | names text | 0 | M1 |
| `pubmed_title_truncated` | 22 | 22 | 22 / 0 / 0 | 22 / 22 (100%) | 8 / 22 (36%) | 2 | M2-enrich |
| `section_lost` | 1 | 1 | 0 / 1 / 0 | 0 / 1 (0%) | names text | 0 | M1 |
| `stage4_group_failures` | 1 | 1 | none | none | names text | 0 | M1 |
| `stage6_render_warnings`: `appendix_grant_too_sparse` | 11 | 11 | 3 / 2 / 1 | 3 / 6 (50%) | names text | 0 | M1 |
| `stage6_render_warnings`: `appendix_no_route` | 4 | 4 | none | none | names text | 0 | M1 |
| `stage6_render_warnings`: `appendix_no_route_T` | 40 | 40 | 2 / 13 / 5 | 2 / 20 (10%) | names text | 0 | M1 |
| `stage6_render_warnings`: `appendix_recovered` | 1 | 1 | 0 / 1 / 0 | 0 / 1 (0%) | names text | 0 | M1 |
| `stage6_render_warnings`: `appendix_recovered_A` | 32 | 32 | 3 / 12 / 5 | 3 / 20 (15%) | names text | 0 | M1 |
| `stage6_render_warnings`: `appendix_t_validation_recoded` | 3 | 3 | none | none | names text | 0 | M1 |
| `stage6_render_warnings`: `bare_dates_in_table` | 1 | 1 | 1 / 0 / 0 | 1 / 1 (100%) | names text | 0 | M1 |
| `stage6_render_warnings`: `board_cert_row_skipped` | 3 | 3 | none | none | names text | 0 | M1 |
| `stage6_render_warnings`: `no_teaching_content` | 5 | 5 | 0 / 0 / 5 | 0 / 5 (0%) | names text | 0 | M1 |
| `stage6_render_warnings`: `reroute_cross_family` | 14 | 14 | 4 / 4 / 1 | 4 / 9 (44%) | 8 / 14 (57%) | 8 | M1 |
| `stage6_render_warnings`: `reroute_refused` | 48 | 0 | 15 / 1 / 0 | 15 / 16 (94%) | 12 / 48 (25%) | 12 | M1 |
| `stage6_render_warnings`: `reroute_same_family` | 10 | 0 | 7 / 0 / 0 | 7 / 7 (100%) | 1 / 10 (10%) | 1 | M1 |
| `stage6_render_warnings`: `semicolon_fused_bullets` | 4 | 4 | 0 / 0 / 2 | 0 / 2 (0%) | names text | 0 | M1 |
| `table_shape` | 12 | 0 | 1 / 2 / 3 | 1 / 6 (17%) | names text | 0 | M1 |
| `taxonomy_code_coverage` | 3 | 0 | none | none | names text | 0 | M1 |
| `teaching_postcheck` | 105 | 42 | 86 / 3 / 16 | 86 / 105 (82%) | 51 / 105 (49%) | 11 | M3b-5c |
| `wrong_start_date` | 9 | 1 | 0 / 1 / 1 | 0 / 2 (0%) | 8 / 9 (89%) | 7 | M1 |

M2-junk added `junk_or_header_row` and changed no other lint's findings on any run. All 104 hits were hand-read against the stage-4 entry and the rendered row: the 39 unmatched are 38 group headers, lead-in labels, date fragments or banner titles printed as records, and 1 partly so (a banner title rendered with a date); of the 65 matched, 64 are and 1 is partly so (a header row that also shows a role). 102 of 104 (98%). The shapes were tuned on this farm, so the next batch is the first out-of-sample check.
M2-enrich added `pubmed_title_truncated` and `enrichment_pubtype_mismatch` and changed no other lint's findings on any run. Every hit was hand-read against the stage-4 title and the accepted text of the base docx (text inside `w:ins` included, `w:delText` excluded). `pubmed_title_truncated`: 22 of 22 are cut titles that render cut, followed directly by the journal; the 14 unmatched sit on 6 runs the autopsies did not record them for. `enrichment_pubtype_mismatch`: 2 of 2 render a correction notice's title where the CV lists the paper; the unmatched one is a CV that gives the notice's PMID for the paper. Both read stage-5 JSON built before #1358, which fixed both causes at the source, so on newer runs they guard against a regression.

No hits on M1's 62 runs: `bucket_status`, `dead_sections`, `invented_records`, `llm_refusal_in_output`, `no_output`, `owner_contact_missing`, `pipeline_errors_present`, `protected_data_in_output`, `python_repr_in_output`, `segmentation`, `stage3b_fallback_ratio`, `stage3b_second_pass_error`, `stage_failure_recorded`, `table_lost`, `under_extraction`, `unrendered_records`.

## Score cap inputs (M3)

The quality score caps a run at 84 on four more lints only while this table records the cap's own findings at 80% precision or more on 20 or more of them (Paul's decision on #822, 2026-10-02). Each row measures the subset of the lint's findings that would cap, not the whole lint. **matched** is M1's index match; every unmatched hit was hand-checked against the rendered docx (`w:ins` text included) and the stage-4 entry. A partial is a real defect other than the one the cap claims, and counts against precision.

| cap input | caps when | hits | matched | hand-checked: TP / partial / FP | precision | caps? |
|---|---|---|---|---|---|---|
| `owner_missing_from_citation`, the hits on runs with 3 or more (XWNZWW 10, BMHBJZ 6, BMAMWE 4, NJIKGI 3) | 3 or more on a run | 23 | 11 | 12 / 0 / 0 of 12 | 23 / 23 (100%) | yes |
| `owner_missing_from_citation`, lint-wide (for reference; 6 of these hits sit on runs with 1 hit and never cap) | | 29 | 13 | 14 / 1 / 1 of 16 | 27 / 29 (93%) | |
| `multi_record_coverage`, WARN with 2 or more clauses or values on no line | 1 or more | 20 | 14, of which 3 are `field_lost`, hand-checked: 1 TP, 2 partial | 2 / 1 / 3 of 6 | 14 / 20 (70%) | no |
| `offschema_fields`, record-shaped WARN | 1 or more | 8 | 6 | 1 / 0 / 1 of 2 (the other was rendered fused into raw text) | 7 / 8, n under 20 | no |
| `dedup_drops` | 1 or more lossy | 13 (6 WARN), 17 drops | names text | of the 17 drops, 7 have their text on one rendered line at 0.9 or more of its words; EBYSBC verdicts 2 / 2 / 4 of 8 | n under 20 | no |

`owner_missing_from_citation`'s partial is a data-safety-board credit dropped from a trial citation, and its false positive a co-presented talk whose rendered line names only the other presenter. Both sit on runs with a single hit, so neither caps. Its 13 matched hits sit on verified findings of the owner-dropped classes (EBYSBC E13 and E32, and s7ab-8); 11 of them are on the capping runs. The unmatched 14 true positives are owner credits the autopsies did not list one by one: eight cut by "et al.", and six study-group or collaborator credits ("including ...") the rendered line leaves out. The doctor reports 30 hits; the 30th is on QFJSXR, the one run with no autopsy labels, so the harness does not score it, and with 1 hit it never caps.

`multi_record_coverage`'s false positives are one record's own detail split across two source lines (a meeting's name and its venue, twice on one run) and a mentee list the docx renders on one line; its partials lose a mentee's career detail, one of two committee roles, or mentees' years while their names render.

## Recall (M1)

All lints together matched 60 of the 487 indexed verified findings (12%). By severity: high 22 of 84 (26%), medium 24 of 222 (11%), low 14 of 181 (8%). By batch: EBYSBC 33 of 312, s7ab 18 of 112, pilot 9 of 63. 29 more verified findings, all from the pilot, carry no entry index: their evidence names none, or names entry numbers the conversion could not resolve. No hit can match them.

Matching by index both under- and over-credits, so compare it with the autopsies' own judgement. EBYSBC and the pilot recorded whether the doctor caught each finding: yes for 46, partly for 17, of 404.

Per-class recall (`by_batch_class` and `by_class_ref`) is in the `--json` output. Recompute it rather than copying it here. Where each batch's classes come from:

- EBYSBC: `batch_class` is the synthesis's own class, `E1`..`E36`, and `class_ref` is the cross-batch class each verified finding cites, mostly an s7ab class (`s7ab-1`..`s7ab-21`).
- s7ab: its verified findings carry no class. The label converter reads the class table in the s7ab synthesis, which names each class's records by uid and entry index in prose. A finding gets `s7ab-<n>`, as both `batch_class` and `class_ref`, when exactly one class row names its uid and one of its indices. When several rows do, it gets one only if exactly one of those rows lists the finding's own category. This assigns 83 of the 112. Each of the 83 was read against the finding's own description, and one was wrong: RXYBVF-04 is set by hand to `s7ab-2`, whose row gives that run a record count rather than an index. Of the other 29, 2 are named by more than one row and 27 by none. Some rows give no index at all: class 1, for instance, names its runs and record counts only. Those 29 are grouped as `(none)`, so per-class recall for s7ab covers 83 findings.
- pilot: `class_ref` is the pilot's `failure_class`. It has no batch class.

## How to re-measure

The farm and the labels hold CV content, so they live outside the repo:

- `~/worktrees/eb-farm/` holds the 63 runs' stage outputs (`outputs/`), their source docx (`src_docx/`) and a base render (`base/`).
- `~/worktrees/eb-labels/<uid>.json` holds one label file per run, converted from each run's verified autopsy (`analysis/<uid>/autopsy_verified.json`, and the pilot's `analysis/pilot/_tools/wf_result.json`), with s7ab's classes read from its synthesis (`docs/analysis/AUTOPSY-s7ab-batch-2026-10-02.md`, local). A label holds ids, codes, severities, entry indices and verdicts, and no CV text. The schema is in `scripts/doctor_vs_autopsy.py`'s docstring.

Run from the worktree of the arm being measured. A doctor-only change can reuse the base render; a change to stage 6 renders first (`scripts/render_gate.py`, see `docs/guides/render-doctor-gates.md`) and passes its own render directory.

```bash
FARM=$(mktemp -d)/farm
~/worktrees/eb-farm/mkdocfarm.sh ~/worktrees/eb-farm/base "$FARM"
PYTHONPATH=src python3 scripts/doctor_gate.py "$FARM" "$FARM-doctor.json" --source-dir ~/worktrees/eb-farm/src_docx
python3 scripts/doctor_vs_autopsy.py "$FARM-doctor.json" ~/worktrees/eb-labels --json "$FARM-score.json"
```

M1 skipped the first two steps: it scored the existing `~/worktrees/eb-farm/doctor_base.json`, a `doctor_gate.py` run of `c3d87c5f` over the base farm (397 findings on 63 runs).

`--json` also lists each lint's unmatched hits (uid and entry index) and the runs of its unlocated hits, which is the sample for a hand-check. A lint PR reports its row before and after, hand-checks up to 30 unmatched hits, and adds a measurement id here.

## History: hand-checks recorded on #819

These come from before this ledger. They are quoted as recorded, on the batch and image named, with "real of checked" as each hand-check counted it. "partly" means a real defect under a wrong description.

| date | lint | real of checked | batch | source |
|---|---|---|---|---|
| 2026-07-15 | all WARNs | ~20% real | corpus sweep | #819 body ("corpus sweep outcome") |
| 2026-07-15 | `missed_headers` | 41% (36 of 61 were the colon bug) | | #292 |
| 2026-08 | `missed_headers` | 1 of 6 | | #539 |
| 2026-09-11 | `missed_headers` | 22 of 42 (52%); 17 are two more normalisation bugs | | #814, #813 |
| 2026-07 | `classified_unrendered` on mapped codes | false whenever the content is in a tracked insertion (`w:ins`) | | #249, #255 |
| 2026-09-12 | `classified_unrendered` | 0 of 1 | one run | #819 comment, 2026-09-12 |
| 2026-09-17 | `protected_data_in_output` | 2 of 2 | Batch 4, 20 CVs, dev `c7daa91` | #819 comment, 2026-09-17 |
| 2026-09-17 | `output_hygiene` (bracket-code leak) | 0 of 1 | Batch 4 | same |
| 2026-09-17 | `segmentation` (coverage) | 1 of 1 | Batch 4 | same |
| 2026-09-17 | `dedup_drops` | 2 of 2 drops (1 finding) | Batch 4 | same |
| 2026-09-17 | `classified_unrendered` | 0 of 6 | Batch 4 | same |
| 2026-09-17 | `table_shape` | 2 of 23 rows, plus 1 borderline (9%) | Batch 4 | same |
| 2026-09-17 | `missed_headers` | at most 4 of 28 (14%) | Batch 4 | same |
| 2026-09-17 | `stage6_render_warnings` (T diverted) | 0 of 15 CVs as a routing gap | Batch 4 | same |
| 2026-09-17 | `stage6_render_warnings` (G refused by the passthrough writer) | 13 misroutes and 1 misclassification in 14 entries, 6 CVs | Batch 4 | same |
| 2026-09-29 | `table_shape` (organization duplicated in name) | 1 of 13 | 109 rendered docx, dev `969f2dd9` | #819 comment, 2026-09-29 |
| 2026-10-01 | `stage6_render_warnings` (no visible teaching content) | 0 of 2 | IPXFBA, 37 CVs, dev-235 `f5e15871` | #819 comment, 2026-10-02 (appendix and render-shape rows) |
| 2026-10-01 | `stage6_render_warnings` (semicolon-fused bullets) | 0 of 2 | IPXFBA | same |
| 2026-10-01 | `classified_unrendered` | 0 of 5 as "not found"; 1 partly real, 1 a real defect under a wrong description (the same 5 findings as the F1, F2 and Q2, S9 rows below) | IPXFBA | same |
| 2026-10-01 | `date_only_lines` | 0 of 1 | IPXFBA | same |
| 2026-10-01 | `wrong_start_date` | 1 of 2, with the wrong cause named | IPXFBA | same |
| 2026-10-01 | `table_shape` | 2 of 4 (1 minor) | IPXFBA | same |
| 2026-10-02 | `dedup_drops` | 3 of 8 (38%); recall 3 of 13 real-loss drops (23%) | IPXFBA | #819 comment, 2026-10-02 |
| 2026-10-02 | `section_lost` on F1, F2, B1 | 0 of 4 as "record lost" | IPXFBA | same |
| 2026-10-02 | `classified_unrendered` on F1, F2 | 0 of 3 | IPXFBA | same |
| 2026-10-02 | `classified_unrendered` on Q2, S9 | 0 of 2 | IPXFBA | same |
| 2026-10-02 | `section_lost` on J | 0 of 1 | IPXFBA | same |
| 2026-10-02 | `stage6_render_warnings` (renderer declined M2A, M2B, M2C) | 6 of 7 CVs | IPXFBA | same |
| 2026-10-02 | `under_extraction` (grants) | 3 of 3 | IPXFBA | same |
| 2026-10-02 | `unrendered_records` (grants) | 0 of 1 | IPXFBA | same |
| 2026-10-02 | `enrichment_failures` | 3 of 7 CVs (43%) | IPXFBA | same |
| 2026-10-02 | `missed_headers` | 8 of 75 (10.7%); 0 of 52 at WARN | IPXFBA | same |
| 2026-10-02 | `segmentation` (coverage) | 1 of 2 | IPXFBA | same |
| 2026-10-02 | `table_lost` | 1 of 1 | IPXFBA | same |
| 2026-10-02 | `under_extraction` | 4 of 4 | IPXFBA | same |
| 2026-10-02 | `segmentation` (mega_entries) | 7 of 9 | IPXFBA | same |
| 2026-10-02 | `output_hygiene` | 11 of 12, 1 partly | pilot, 12 CVs, dev-236 `e59aaf44` | #819 comment, 2026-10-02 (pilot) |
| 2026-10-02 | `stage6_render_warnings` (A and T) | 14 of 18, 4 partly | pilot | same |
| 2026-10-02 | `enrichment_failures` | 5 of 5 | pilot | same |
| 2026-10-02 | `missed_headers` | 3 of 3 | pilot | same |
| 2026-10-02 | `dedup_drops` | 1 of 2; recall 1 of 6 CVs | pilot | same |
| 2026-10-02 | `table_shape` | 0 of 3; recall 0 of 1 | pilot | same |
| 2026-10-02 | `pipe_leaks` | 0 of 1 | pilot | same |
| 2026-10-02 | `duplicate_records` | 0 of 1, 1 partly | pilot | same |
| 2026-10-02 | `wrong_start_date` | 0 of 1, 1 partly | pilot | same |
| 2026-10-02 | `date_only_lines` | 1 of 1 | pilot | same |
| 2026-10-02 | all lints (recall) | 12 of 92 verified loss records; 1 of 19 whole-record losses | pilot | same |
| 2026-10-02 | `field_loss` (draft, not shipped) | 20 of 112 (18%) | 118 farm CVs and 20 production runs | #819 comment, 2026-10-02; #817 |
| 2026-10-04 | `teaching_postcheck`, every hit (M3b-5c) | WARN: 39 of 42 real, 3 partly (a 5c line stage 6 dedup then dropped); INFO (role only): 47 of 63 real, 16 a role the entry implies (talks to residents read as Presenter) | the 63-run farm, origin/dev `fb466a0f` | PR body, `feat/ebysbc-doctor-5c-and-contact` |
| 2026-10-04 | `contact_slot_lost`, every hit | 1 of 1 on the `fb466a0f` render; 9 of 9 on the dev-242 documents the EBYSBC autopsy read | the 63-run farm | same |
