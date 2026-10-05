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
| M1-dup | 2026-10-04 | the `dedup_drops` / `duplicate_records` extension, over the base render of origin/dev `fb466a0f` (`~/worktrees/eb-farm-w3b/base`) | as M1 | as M1 | #446, #666 |
| W3B-SC | 2026-10-04 | origin/dev `fb466a0f` plus `section_consistency` and `segmentation_collapse`, over the base render of `fb466a0f` | 62 of the 63-run farm, as M1 | as M1 | EBYSBC E11/E30/E17 |

W3B-SC hand-checked every hit of its two lints, not only the unmatched ones. The first version of `section_consistency` had 29 hits, and a blind verifier found one false positive: MQSUIC 241, a bare bibliography URL coded S0, which `src/unified_pipeline/core/taxonomy_v7.json` lists as a typical S0 entry. That made it 28 of 29 (97%). The bare-URL branch of `cross_reference_as_record` now allows S0, which removes exactly that hit, so the shipped lint is 28 of 28 (every hit checked). The 14 unmatched `section_consistency` hits are 7 grant-review headings whose rows are coded Q2, 1 grant-review heading whose rows are coded I (SJWASY), 4 journal guideline or protocol articles coded S5 under a peer-reviewed heading or among peer-reviewed siblings (RGUNJV, VYICGW, ZGBCIT twice), and 2 BLS/ACLS/PALS lines coded F1/F2. The rules were written from these same 63 runs, so the precision is in-sample.

The RCBKFG autopsy (2026-10-05) added a `training_as_degree` shape to `section_consistency`: an intern or resident row coded B1 (#1415). Over the 96 local analysis runs with stored stage JSON (83 distinct source documents), rendered and doctored from origin/dev `8cafcd4c` with and without it, it adds one finding, on no other run, and changes no other finding: GKAQHB, entries 17 and 20, which that autopsy verified (its N1). The `section_consistency` row below still describes the W3B-SC measurement.
| W3B-GR | 2026-10-04 | origin/dev `fb466a0f` plus `grant_boundary` and `grant_bucket`, over the base render of `fb466a0f`; re-run on origin/dev `89ee30f7` with the same 45 and 16 hits | 62 of the 63-run farm, as M1 | as M1 | #1226, #1343 |

W3B-GR added `grant_boundary` and `grant_bucket` and changed no other lint's findings on any run. Every unmatched hit was hand-read against the stage-4 grant list and the source document's line order. `grant_boundary`: of its 18 unmatched hits, 13 are one ZCTARO list whose records open with Source and whose stage-2 cut slipped one line from entry 304 on, so each later grant carries the next grant's title; 2 (VGHNZD 1496, 1504) are the drift verified finding VGHNZD-01 describes, which names only 1488 by index; 1 (VGHNZD 1546) opens with a title whose sponsor line sits in the entry before; 1 (SDEBQJ 291) opens with the PI and end-date line of the grant before it. The 18th (RXYBVF 502) is doubtful: the source holds that title alone after the last numbered grant, so no record visibly lost it; it is counted as a false positive. 44 of 45 (98%). `grant_bucket`: all 16 hits match a verified finding (ZDCXIV-01, 15 grants; KYOPUV-05, entry 589). The shapes were written from these same 63 runs, so the precision is in-sample.

The EBYSBC verdicts were given on the doctor deployed for that batch (dev-242, `f4f087fc`). On M1's code every judged (run, lint) pair still fires, so the verdict columns describe the same findings as the hit columns.

## Per-lint precision

| lint | hits | warn+ | judged TP / partial / FP | TP / judged | matched / hits | caught | measured |
|---|---|---|---|---|---|---|---|
| `classified_unrendered` | 1 | 0 | 0 / 0 / 1 | 0 / 1 (0%) | names text | 0 | M1 |
| `contact_slot_lost` | 1 | 1 | 1 / 0 / 0 | 1 / 1 (100%) | 1 / 1 (100%) | 1 | M3b-5c |
| `date_only_lines` | 2 | 0 | none | none | names text | 0 | M1 |
| `dedup_drops` | 14 | 7 | 2 / 2 / 4 | 2 / 8 (25%) | 3 / 14 (21%) | 3 | M1-dup |
| `duplicate_passages` | 1 | 1 | none | none | names text | 0 | M1 |
| `duplicate_records` | 11 | 11 | 4 / 0 / 1 | 4 / 5 (80%) | 1 / 9 (11%) | 1 | M1-dup |
| `enrichment_failures` | 15 | 15 | 3 / 4 / 0 | 3 / 7 (43%) | names text | 0 | M1 |
| `enrichment_pubtype_mismatch` | 2 | 2 | 2 / 0 / 0 | 2 / 2 (100%) | 1 / 2 (50%) | 1 | M2-enrich |
| `grant_boundary` | 45 | 45 | none | 44 / 45 hand-checked or matched (98%) | 27 / 45 (60%) | 11 | W3B-GR |
| `grant_bucket` | 16 | 16 | none | 16 / 16 matched (100%) | 16 / 16 (100%) | 2 | W3B-GR |
| `implausible_year` | 21 | 21 | 1 / 0 / 1 | 1 / 2 (50%) | 7 / 21 (33%) | 5 | M1 |
| `junk_or_header_row` | 104 | 104 | 102 / 2 / 0 | 102 / 104 (98%) | 65 / 104 (63%) | 20 | M2-junk |
| `llm_fallback_served` | 1 | 1 | 1 / 0 / 0 | 1 / 1 (100%) | names text | 0 | M1 |
| `missed_headers` | 21 | 0 | 4 / 2 / 2 | 4 / 8 (50%) | names text | 0 | M1 |
| `offschema_fields` | 47 | 14 | 16 / 2 / 5 | 16 / 23 (70%) | 34 / 47 (72%) | 28 | M1 |
| `output_hygiene` | 55 | 0 | 30 / 3 / 0 | 30 / 33 (91%) | names text | 0 | M1 |
| `pipe_leaks` | 5 | 5 | 0 / 0 / 2 | 0 / 2 (0%) | names text | 0 | M1 |
| `pubmed_title_truncated` | 22 | 22 | 22 / 0 / 0 | 22 / 22 (100%) | 8 / 22 (36%) | 2 | M2-enrich |
| `section_consistency` | 28 | 28 | none | 28 / 28 hand-checked (100%) | 14 / 28 (50%) | 14 | W3B-SC |
| `section_lost` | 1 | 1 | 0 / 1 / 0 | 0 / 1 (0%) | names text | 0 | M1 |
| `segmentation_collapse` | 1 | 1 | none | 1 / 1 hand-checked (100%) | 1 / 1 (100%) | 1 | W3B-SC |
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

The quality score caps a run at 84 on more lints only while this table records the cap's own findings at 80% precision or more on 20 or more of them (Paul's decision on #822, 2026-10-02). Each row measures the subset of the lint's findings that would cap, not the whole lint. **matched** is M1's index match; every unmatched hit was hand-checked against the rendered docx (`w:ins` text included) and the stage-4 entry. A partial is a real defect other than the one the cap claims, and counts against precision.

| cap input | caps when | hits | matched | hand-checked: TP / partial / FP | precision | caps? |
|---|---|---|---|---|---|---|
| `owner_missing_from_citation`, the hits on runs with 3 or more (XWNZWW 10, BMHBJZ 6, BMAMWE 4, NJIKGI 3) | 3 or more on a run | 23 | 11 | 12 / 0 / 0 of 12 | 23 / 23 (100%) | yes |
| `owner_missing_from_citation`, lint-wide (for reference; 6 of these hits sit on runs with 1 hit and never cap) | | 29 | 13 | 14 / 1 / 1 of 16 | 27 / 29 (93%) | |
| `multi_record_coverage`, WARN with 2 or more clauses or values on no line | 1 or more | 20 | 14, of which 3 are `field_lost`, hand-checked: 1 TP, 2 partial | 2 / 1 / 3 of 6 | 14 / 20 (70%) | no |
| `offschema_fields`, record-shaped WARN | 1 or more | 8 | 6 | 1 / 0 / 1 of 2 (the other was rendered fused into raw text) | 7 / 8, n under 20 | no |
| `dedup_drops` | 1 or more lossy | 13 (6 WARN), 17 drops | names text | of the 17 drops, 7 have their text on one rendered line at 0.9 or more of its words; EBYSBC verdicts 2 / 2 / 4 of 8 | n under 20 | no |

`owner_missing_from_citation`'s partial is a data-safety-board credit dropped from a trial citation, and its false positive a co-presented talk whose rendered line names only the other presenter. Both sit on runs with a single hit, so neither caps. Its 13 matched hits sit on verified findings of the owner-dropped classes (EBYSBC E13 and E32, and s7ab-8); 11 of them are on the capping runs. The unmatched 14 true positives are owner credits the autopsies did not list one by one: eight cut by "et al.", and six study-group or collaborator credits ("including ...") the rendered line leaves out. The doctor reports 30 hits; the 30th is on QFJSXR, the one run with no autopsy labels, so the harness does not score it, and with 1 hit it never caps.

`multi_record_coverage`'s false positives are one record's own detail split across two source lines (a meeting's name and its venue, twice on one run) and a mentee list the docx renders on one line; its partials lose a mentee's career detail, one of two committee roles, or mentees' years while their names render.

### Zero-false-positive WARN lints (M4-cap, 2026-10-05)

The RCBKFG autopsy (6 runs, dev-247) found 0 false positives for `grant_boundary`, `grant_bucket`, `junk_or_header_row` and `etal_added`, and Paul approved feeding them into the cap on 2026-10-05. Each row pools that batch with the ledger rows above and with the NDMRSO autopsy (30 runs, dev-246), whose per-run `doctor_review` judged these lints on the dev-HEAD doctor; NDMRSO is the first batch these shapes were not written from. Hits are counted with origin/dev `10f18e34`'s lints over each run's stored stage-4 JSON and docx (`analysis/<uid>/`), and over the EBYSBC farm's base render of `fb466a0f`. A hit "matched" names an entry a verified autopsy finding lists; an unmatched hit was read by hand. A partial counts against precision.

| cap input | caps when | lint-wide TP / judged | cap subset: TP / judged | caps? |
|---|---|---|---|---|
| `grant_boundary` | 3 or more on a run | 68 / 70 (97%): farm 44 / 45 (W3B-GR), RCBKFG 14 / 14, NDMRSO 10 / 11 | 59 / 59: ZCTARO 13, CXRYCF 10, CTWLTR 8, VGHNZD 5 (farm); KUUKNJ 13 (RCBKFG N1); DXAGUS 6 (DXAGUS-01/-02), VYRDHN 4 (VYRDHN-02) | yes |
| `grant_bucket`, an application rendered as an award | 1 or more | 30 / 30 (100%): ZDCXIV 15 (ZDCXIV-01), FLYBMX 15 (RCBKFG); one CV in two runs | same 30 / 30 | yes |
| `grant_bucket`, a Current grant whose end date has passed | | 2 / 2: KYOPUV 589 (farm), CAOACN 589 (RCBKFG) | n under 20, and the shape reads today's date | no |
| `junk_or_header_row` | 5 or more on a run | 149 / 165 (90%): farm 102 / 104 (M2-junk), RCBKFG 9 / 9, NDMRSO 38 / 52 (73%) | 100 / 105 (95%): farm 69 hits on 6 runs, the 2 M2-junk partials counted as if they sit there; NDMRSO KHXOUF 12 / 15, BNYLDF 11 / 11, SYWZJA 5 / 5, GCFEBE 5 / 5 | yes |
| `etal_added` (INFO) | | RCBKFG 92 / 92; NDMRSO 17 judged lines, 0 false | | no: see below |

`grant_boundary`'s two false positives (RXYBVF 502, VYNARH 648) sit on runs with 1 and 2 hits, and a lone hit is one grant's edge; a slipped stage-2 cut carries down the list, which is what 3 or more catches. The NDMRSO 10 / 11 is DXAGUS 6 (judged TP) and VYRDHN 4 (each names a VYRDHN-02 record), against VYNARH 648.

`junk_or_header_row` out of sample is the weakest number here: NDMRSO judged 22 TP, 3 partial and 11 FP over its reviewed runs (EHGXAL 3 of 3 false: trainee headings that do group their awards; UYQRUN 2 of 3 quote a row that is not the entry's own, KHXOUF 3 of 15 quote another row or name an entry stage 6 dropped; REOYVH and SVYSGY 1 of 1). BNYLDF's 11 and GCFEBE's 5 were not in that review and were read for this row: BNYLDF 9 are BNYLDF-03 and 2 are military-award lead-in labels printed as rows; GCFEBE's 5 are GCFEBE-06. Every NDMRSO false positive sits on a run with 1 to 3 hits, so the cap needs 5.

`etal_added` qualifies on precision but does not cap. It is INFO, not WARN: the owner stays on every line it names, and a citation that lost the owner is `owner_missing_from_citation`'s, which already caps. It fires on 88 of the 96 stored runs, so a cap at 1 would mark nearly every run for one systemic stage-5d rule rather than for anything run-specific. And #1404 (merged 2026-10-05, not yet deployed) removes that rule from the 5d prompt, so on new runs the lint should go quiet; a cap now would mostly re-mark stored runs.

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
| 2026-10-04 | `duplicate_records` record rule (title, PMID or DOI; one grant across M2A/M2B/M2C) | 16 of 18 entry pairs (89%), every pair read; 7 of 9 findings | base render of `fb466a0f`, 62 runs | M1-dup |
| 2026-10-04 | `dedup_drops` occasion test (a month, day, part or numeral the kept entry lacks) | 9 of 9 drops, every one read: 1 on the `fb466a0f` render, 8 more on the dev-242 render the batch was autopsied on | EBYSBC farm | M1-dup |
