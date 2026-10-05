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
| JUNK-EV | 2026-10-05 | `fix/doctor-junk-row-evidence` merged with origin/dev `43f84e1e`, against `43f84e1e`, both over the same stored renders (`run_doctor` per run; `scripts/doctor_gate.py` for the 126 runs) | 294: the 105 runs under `analysis/<uid>` with stage-4 JSON and a stage-6 docx, the 63-run EBYSBC/s7ab/pilot farm (`doctor_base_farm`, with its source docx), and the 126 farm and batch runs (the RCB-D2 render, no `--source-dir`) | the EOAHMI recheck (QTATUP) and the NDMRSO per-run `doctor_review` (KHXOUF) | #985 |

W3B-SC hand-checked every hit of its two lints, not only the unmatched ones. The first version of `section_consistency` had 29 hits, and a blind verifier found one false positive: MQSUIC 241, a bare bibliography URL coded S0, which `src/unified_pipeline/core/taxonomy_v7.json` lists as a typical S0 entry. That made it 28 of 29 (97%). The bare-URL branch of `cross_reference_as_record` now allows S0, which removes exactly that hit, so the shipped lint is 28 of 28 (every hit checked). The 14 unmatched `section_consistency` hits are 7 grant-review headings whose rows are coded Q2, 1 grant-review heading whose rows are coded I (SJWASY), 4 journal guideline or protocol articles coded S5 under a peer-reviewed heading or among peer-reviewed siblings (RGUNJV, VYICGW, ZGBCIT twice), and 2 BLS/ACLS/PALS lines coded F1/F2. The rules were written from these same 63 runs, so the precision is in-sample.

The RCBKFG autopsy (2026-10-05) added a `training_as_degree` shape to `section_consistency`: an intern or resident row coded B1 (#1415). Over the 96 local analysis runs with stored stage JSON (83 distinct source documents), rendered and doctored from origin/dev `8cafcd4c` with and without it, it adds one finding, on no other run, and changes no other finding: GKAQHB, entries 17 and 20, which that autopsy verified (its N1). RCB-HE re-ran the lint on origin/dev `6592d5eb` over RCB-D's 69 runs (each run's stored stage-3b JSON): `training_as_degree` fires once there too, on GKAQHB, and on none of the 62 labelled runs. That finding names two entries and counts as one hit, as RCB-D's JJUQDF finding (entries 342-346) does. The `section_consistency` row below counts it: 30 of 30 hand-checked. GKAQHB has no label file, so the hits, matched and caught columns, which describe the 62 labelled runs, do not change.
| W3B-GR | 2026-10-04 | origin/dev `fb466a0f` plus `grant_boundary` and `grant_bucket`, over the base render of `fb466a0f`; re-run on origin/dev `89ee30f7` with the same 45 and 16 hits | 62 of the 63-run farm, as M1 | as M1 | #1226, #1343 |
| RCB-SC | 2026-10-05 | origin/dev `5e6eac1d` plus `span_count`, over the base render of `5e6eac1d` (`scripts/render_gate.py`, then `scripts/doctor_gate.py`) | 245 stored runs with stage-4 JSON: 106 under `analysis/<uid>`, 13 under `analysis/pilot/<uid>/outputs`, and the 126-run farm and 2026-09-11/-17 batches (66 in `src/unified_pipeline/outputs`, 40 and 20 under `_autopsy_artifacts`) | the RCBKFG per-run autopsies (UYFRTL-N1) and EQADVR-05 | #1245 |

W3B-GR added `grant_boundary` and `grant_bucket` and changed no other lint's findings on any run. Every unmatched hit was hand-read against the stage-4 grant list and the source document's line order. `grant_boundary`: of its 18 unmatched hits, 13 are one ZCTARO list whose records open with Source and whose stage-2 cut slipped one line from entry 304 on, so each later grant carries the next grant's title; 2 (VGHNZD 1496, 1504) are the drift verified finding VGHNZD-01 describes, which names only 1488 by index; 1 (VGHNZD 1546) opens with a title whose sponsor line sits in the entry before; 1 (SDEBQJ 291) opens with the PI and end-date line of the grant before it. The 18th (RXYBVF 502) is doubtful: the source holds that title alone after the last numbered grant, so no record visibly lost it; it is counted as a false positive. 44 of 45 (98%). `grant_bucket`: all 16 hits match a verified finding (ZDCXIV-01, 15 grants; KYOPUV-05, entry 589). The shapes were written from these same 63 runs, so the precision is in-sample.
| RCB-D | 2026-10-04 | `fix/rcbkfg-doctor-precision` on origin/dev `8cafcd4c`, over a fresh base render of `8cafcd4c` (`render_gate.py`, 69 of 69 rendered) | 62 of the 63-run farm, as M1, plus the 6 RCBKFG runs (JJUQDF, KUUKNJ, FLYBMX, CAOACN, GKAQHB, UYFRTL), which have no label file and were hand-checked against their per-run autopsies | as M1 | RCBKFG (d), #1243, #446, #729, #222, #1222 |

RCB-D is the RCBKFG batch's doctor section (d). It changes seven lints and leaves every other lint's findings unchanged on all 69 runs: base and branch differ in 24 findings, each listed here and each hand-read against the stage-4 entry and the rendered docx (`w:ins` text included).

- `multi_record_coverage` (+3, all true): it now reads month/two-digit-year dates ("7/05") and apostrophe years ("'96"). CAOACN 32 and 64 (RCBKFG N1, KYOPUV-01/02: 4 appointments and 3 task forces lost) and YOXXOH 93 (verified YOXXOH-uid-4, a second training program lost). CAOACN 62 and 88 stay silent: 62 has `stage4_records` and four-digit years, and 88 is undated with no title word.
- `junk_or_header_row` (+7, all true): `role_only` (a duty sentence rendered as a row holding only the role stage 4 read: GKAQHB and MRJDWE 79, 84, verified MRJDWE-07), `description_only` (a description-only record whose sentence fills the committee row: GKAQHB 94), and a lead-in label rendered with its first word cut (JJUQDF and AQCLHS 326, verified AQCLHS-05). A first `role_only` draft, with `title` as a role field and no prose test, fired 55 times on D1, K1 and R rows that are records; the shipped rule fires only on the 4 above.
- `duplicate_records` (+3, all true): two bodies one letter apart (not a digit, 60 characters or more) are one record. UYFRTL and EQADVR (same source; RCBKFG N6, two abstracts the CV lists twice) and VVRTUC (one book chapter listed twice, same authors, book and pages, one title with a plural).
- `section_consistency` (+1, true): new shape `reviewer_journal_as_editorial_board`, JJUQDF 342-346 (RCBKFG NEW-01). On the farm, every other Q4C entry with no board or editor word sits under a heading that names a board, or names a role ("Member, ... Panel", BFSUMA 170, which stays quiet).
- `wrong_start_date` (-7): a grant renders "-Present" only where its source leaves the start year open, so the lint asks `format_date_range` with the entry's text, as the grant renderer does. FLYBMX and ZDCXIV 157, 174, 175 render one award date (RCBKFG FLYBMX: 0 of 3), and RGUNJV 314 now renders "2002-2003, 2006" with no "-Present", so the shape the verified RGUNJV-06 described is gone from the current render. The one recall match lost (RGUNJV-06, s7ab-13) is that finding.
- `enrichment_failures` (-1): a `title_check_failed` whose `enrichment_rejected.shared_pmid_with` is set is stage 5 putting the CV's own citation back; JJUQDF 93 is that correct rejection (RCBKFG, the fix for AQCLHS-02).
- `contact_slot_lost` (1 replaced): CAOACN 3 no longer names the home number stage 4 also filed as `home_phone`, and now names the office address filed under `work_address` beside the home `address`, which the rendered Office address row lacks (RCBKFG; appended to #1222).

The 62 labelled runs score as before except: `junk_or_header_row` matched 65 to 68, `multi_record_coverage` matched 84 to 85, `wrong_start_date` hits 9 to 5 (matched 8 to 5), `duplicate_records` unlocated 2 to 4, and recall 191 to 190 of 487 (the RGUNJV-06 match above). Every new shape was written from these runs, so the precision is in-sample; the next batch is the first out-of-sample check.
| RCB-D2 | 2026-10-05 | `fix/rcbkfg-doctor-precision-followup` on origin/dev `6592d5eb`, over the RCB-D base render of `8cafcd4c`, and over the blind review's render of origin/dev `10f18e34` for the 126 farm and batch runs RCB-D did not score | the 69 RCB-D runs, plus 126 more: 66 runs under `src/unified_pipeline/outputs` and 60 from the 2026-09-11 and 2026-09-17 `_autopsy_artifacts` batches (no `--source-dir`, so the segmentation lints are blind there) | as M1 | RCBKFG (d), #1243, #446, #1222 |

RCB-D2 is the out-of-sample check of RCB-D that its blind review asked for. On the 126 runs, RCB-D's code added 9 findings and raised 3 `duplicate_records` counts by 4 pairs. Each was hand-read against the stage-4 entry and the rendered docx (`w:ins` text included). 3 of the 13 were true:

- `multi_record_coverage` (+4, none true). Three WARNs read a two-digit date that is a note on the record as a second record: a grant's no-cost extension "through 06/09" (2068 537), a degree "conferred 6/83" (web187 16), and "the aftermath of 9/11" (web200 153). The fourth, an INFO on web199 776, split a mentee record at its "8/02". Each of these entries also writes a four-digit year. RCB-D2 reads a two-digit date only in an entry that writes no four-digit year, which removes all 4. The RCB-D true positives (CAOACN 32 and 64, YOXXOH 93) write none.
- `junk_or_header_row` `role_only` (+4, 2 true). web200 150 (a duty sentence rendered as its role alone) and web204 277 (an award's name lost; the row shows only the role) are true. web40 150 and 154 are false: L2 projects whose name and `launch_date` render on the line above the role sentence. RCB-D2 counts the schema's other one-date fields (`launch_date`, `issue_date`, and so on) as dated, which removes those 2.
- `duplicate_records` (+4 pairs: web218, web227 twice, web244; none true). Each is one abstract or talk the CV lists at two meetings. The render prints authors, title and year only, so the two bodies end up one letter apart (a spelling, an author initial, a group name). RCB-D2 applies the one-letter rule only to a body that names a volume or page. The RCB-D pairs (UYFRTL, EQADVR, VVRTUC) all do.
- `section_consistency` (+1, true). `reviewer_journal_as_editorial_board` on web46: 8 journals coded Q4C under 'Personal Data', after an "Ad hoc Reviewer" line.

With RCB-D2, base (origin/dev `6592d5eb`) and branch differ on the 126 runs in exactly the 10 false or doubtful findings above: 6 findings fewer (2,213 to 2,207), and the 3 `duplicate_records` counts back to what they were before RCB-D, and on the 69 RCB-D runs in none (1,806 findings both). `contact_slot_lost` also skips an office-keyed address that repeats the home `address`, as stage 6 does since #1425; it moved no finding on either set. The per-lint rows below still count the 69 RCB-D runs only: the 126-run verdicts are on unlabelled runs and stay in this paragraph.

RCB-SC added `span_count` and changed no other lint's findings on any of the 245 runs (base code against branch code, both over the base render). It fired 118 times: 70 on the 119 `analysis` runs (61 top-level, 9 pilot) and 48 on the 126 farm and batch runs. Every hit was read against its entry's source text, and the lint itself checks that the record's rendered line shows the envelope. 111 are a row showing one range over years or terms the source lists separately with a gap: lists of reviewing or committee years, course offerings by term, and two records fused into one entry (NJIKGI 134, VYNARH 152, VQFSDI 147). 7 are counted false: KYOPUV 42 and 43, a certification and its recertification, which really are one continuous span; CYOFWJ 66, a membership held since its first year whose sub-roles carry the other years; OIYKZE 420, a range whose two ends a table cell split, with 'and' inside the course name between them; web36 449, one range written 'May, 2015 – November, 2019', whose month commas read as a list; and web227 738 and 743, two elected offices listed as '2018, 2021' and '2020, 2023', which may be terms rather than separate years (doubtful, counted false). 111 of 118 (94%): 66 of 70 on the `analysis` runs, 45 of 48 on the farm and batch runs. Some runs are the same CV run twice (UYFRTL and EQADVR, NTCULM and QFJSXR, GKAQHB, MRJDWE and RWBQKF), so their hits are counted once per run. Read by one reviewer, not an adversarial verifier. The parsing rules (months and term words, 'to' and 'between ... and', the list separator, the skip of mentoring codes) were written from the `analysis` runs, so 66 of 70 is in-sample; the farm and batch runs were not used to write them, and 45 of 48 there is the out-of-sample figure. Recall on the verified findings: UYFRTL 33, 34 and 48 and EQADVR 48 fire; UYFRTL 49 does not, because `_record_lines` finds no line for it. On the branch's own render (the stage-6 envelope fix in the same change) those 4 stop firing and 114 hits remain on 64 runs. 107 of them are real, on 61 runs, and every one of those records carries only `start_date` and `end_date`, with no further-span key, so stage 6 has nothing to show in place of the range. That residual is on #1245.

| RC-ROLE | 2026-10-05 | origin/dev `8cafcd4c` plus `role_consistency`, over each run's stored stage-4 JSON (the lint reads nothing else) | 232: the 106 runs under `analysis/` with a stage-4 artifact (which include the 6 RCBKFG runs), plus the 126 farm / batch-3 / batch-4 runs | the RCBKFG per-run autopsies | #1403 |

RC-ROLE: every hit was hand-read against the grant's own text. On the 106 `analysis/` runs, 3 hits on 2 runs: KUUKNJ 243 and 247 are verified finding KUUKNJ N8 (the owner listed under "PIs:" rendered co-I, and listed under "co-Is:" rendered PI). ZCTARO 247 is the same CV's earlier run, which the KUUKNJ report already names as wrong there; it is matched by reading, not by a verified finding of its own. Those shapes were written from those runs, so 3 of 3 is in-sample. The 126 farm/batch runs were out of sample, and the first version of the lint gave 14 hits there, 12 of them true: web188 33 (the owner's name followed by "(PI)", rendered co-I) and 11 web30 grants (the owner as "Name of PD/PI", or as "(MPI)" beside another PI, all rendered co-I). That is 12 of 14 (86%) out of sample and 15 of 17 (88%) overall. The 2 false positives were label shapes the first version misread: web26 705 "Co PI: <owner>" (a co-PI written with a space, read as "PI:") and web241 150 "(PI: <other>, Subcontract PI: <owner>)" over "Role: Co-Investigator" (a subaward's PI, read as the grant's PI). Both labels now name no role, which leaves 15 hits, all true. That 15 of 15 counts the two fixed shapes, so it is not out of sample. Of the 280 grants on the first 97 `analysis/` runs whose text gives the owner exactly one role, 197 agree with `pi_role`, 3 contradict it, and the rest carry an empty or other role, which the lint does not judge. CAOACN N4 (owner named PI, `pi_role` empty) is that unjudged case: an empty role lands on 58 grants across 13 of those runs, 53 of them with the owner in `pi_name`, so it is not reported.
| RC-ROLE2 | 2026-10-05 | origin/dev `43f84e1e` plus four more `role_consistency` shapes (`scripts/doctor_gate.py`, base and branch over the same render) | 245: the 119 runs under `analysis/` and `analysis/pilot/` with stage-4 JSON, each over its stored docx and over a render of `43f84e1e` (`scripts/render_gate.py`, 119 of 119), plus the 126 farm / batch-3 / batch-4 runs over a render of `43f84e1e` (126 of 126) | the EOAHMI recheck's verified #1410 regressions (`analysis/<uid>/recheck_verified.json`) | #1403 |

RC-ROLE2 adds the four grant-table shapes the EOAHMI recheck found and no lint flagged (#1410 side effects). Each finding names its shape in the message. Base and branch differ only in `role_consistency` findings, on every run and arm: the 15 RC-ROLE findings come back with the shape name in their message, and the new findings are the hits below; no other lint's findings changed (stored docx 4,202 to 4,361 findings; `analysis/` render 3,241 to 3,259; farm render 2,267 to 2,284). The stage-6 change in the same PR only names the grant table's row labels as constants, and a render of the 126 farm runs from the branch matches the base render (`render_gate_compare.py`: paragraph CHANGED 0, fingerprint CHANGED 0). Every hit was read against its entry's source text, except the `pi_cell_empty` hits that carry a PI label, which were checked by script (no PI label in any of them names a person other than the owner) and sampled by hand (26 read).

- `pi_cell_empty` (WARN): a rendered grant table whose "Your role:" says PI and whose "Name of Principal Investigator:" is empty. It reads the render, because #1446 now fills that cell with the owner on a bare "PI" role. On the stored documents it fires 143 times on 24 of the 119 `analysis/` runs, including all 11 JIJRSN trials of JIJRSN-01; on the current render it fires twice there (TVZDVF 71 and 76, "Flaherty/<other>, MPI", whose MPIs render only as co-investigators) and 16 times on 4 farm runs ("MPI" or "Contact PI" roles, which #1446 deliberately leaves unfilled). 157 of 159 are true. The 2 partials are a real defect other than the one named: NDXXAD 638 (stage 4 invented the PI role, the cviche-d0 watch item) and web205 841 (the source says Co-PI; the role renders Contact PI). 99%, n=159: WARN.
- `pi_also_co_i` (INFO): `pi_name` is one of the people `co_investigators` lists, and the table renders both rows. 7 hits, all true: KDAZOM 363, 364, 365 and 380 (the PI repeated under Co-Investigators, with "Dr." or ", PI") and MQSUIC 153, 161 and 163 (a student co-PI rendered as the PI of the owner's faculty-PI award). The first version also fired on 30 web204 grants whose co-investigator list is the source's author line, headed by the PI and naming the owner; the PI cell there is right, so those count as partial, and the shape now skips that list (in-sample). n=7: INFO.
- `pi_from_collaborator` (INFO): `pi_name` is a person the text names only after "with" ("with Dr. X"), where no role word labels anyone and no role is stated for the owner. 5 hits, all true, all JIJRSN: 150, 152, 154 and 156 (JIJRSN-02) and 148, the same shape in a record the recheck did not list (its twin VYNARH 148 already named the collaborator as PI). n=5: INFO.
- `owner_lead_as_co_i` (INFO): an unlabelled grant with no `pi_name` and no stated role, whose `co_investigators` list and text both name the owner first. 5 hits, all true: QTATUP 529, 533 and 537 (QTATUP-01), BMHBJZ 644 and LDPDKA 91, both "<owner>, <other>" lines with no label. The first version also fired on NDXXAD 411, where the owner's stated role, "Program Partner", is what renders; the shape now needs no stated role. n=5: INFO.

Recall on the EOAHMI recheck's #1410 regressions in grant tables (JIJRSN-01, JIJRSN-02, QTATUP-01, DUTAVD-04), read against each run's stored stage-4 JSON and docx: JIJRSN-01, 11 of 11 records (`pi_cell_empty`); JIJRSN-02, 4 of 5 records (150, 152, 154, 156 by `pi_from_collaborator`; 156 would also be `pi_also_co_i`, which the entry reports once); QTATUP-01, the 3 of 9 grants where the owner is listed first (`owner_lead_as_co_i`). Not caught: JIJRSN 562 (the owner is "Principal Investigator in" a trial "initiated by Dr. <other>", and stage 4 made the initiator `pi_name`), QTATUP's 6 grants led by someone else (an empty PI cell on a grant the owner did not lead names no owner role to check), and DUTAVD-04 (a plural "Investigators" label left `pi_role` empty, which no shape judges). LOOTTE-01 is a mentee field, not a grant. On the dev-248 documents the EOAHMI doctor caught 0 of these; this catches 3 of the 4 grant findings, at 18 of their 26 records. Every shape was written from these runs, so this precision is in-sample.
| SC-1 | 2026-10-05 | origin/dev `8b287ec2` plus `split_child_unsourced` (`scripts/doctor_gate.py`, base and branch over the same renders, no `--source-dir`) | 111: fresh renders of `8b287ec2` of the EBYSBC/s7ab/pilot farm (63), the EOAHMI farm (9) and the NDMRSO farm (30), plus the 9 EOAHMI runs over their dev-248 render (`6d9862f0`, before #1449), which is where the recheck saw the defects | the EOAHMI recheck (`analysis/<uid>/recheck_verified.json`: DUTAVD-03, WYMVGU-02) | #1445 |

SC-1 adds `split_child_unsourced`: a record stage 4 split out of an entry (`stage4_records`, #1406) that shows a place or a date range from outside its own text. Each finding names its shape. Base and branch differ only in this lint's findings, on every run and arm: EBYSBC/s7ab/pilot 1,830 findings both, NDMRSO 795 both, EOAHMI 125 to 126, EOAHMI on dev-248 138 to 141. Every hit was read against the stage-4 entry and the rendered row (read through `read_docx_table_rows`).

- `institution_from_outside_entry` (INFO): a record with no institution or organization, whose siblings name one, renders in a row with a place cell holding two or more words the entry's text does not. 1 hit, true: DUTAVD 30 on dev-248 (DUTAVD-03, the head-and-neck residency shown with the internship's hospital). It does not fire on the fresh render: #1449 stopped the training institution recovery there, so on current dev it is a regression guard. The first draft, at one foreign word, also fired on DUTAVD 88 on both arms, whose one-word cell is the "Member" role stage 6 supplies; two words removes it. Records whose siblings name no place either are skipped, because there the place comes from an employer or society heading above the entry: without that rule GHCIXA 70 (the society line above it) and ATUVAL 57 (the employer line above it, 4 records) fire, both correct renders, and so does DUTAVD 42, whose bare "Member" record matches another entry's "Member" row of the same year. n=1: INFO.
- `date_from_sibling` (INFO): two or more records carry one range that the entry writes fewer times than that, beside a record whose own range starts outside it, and, with the docx read, two of them render it. 3 hits, all true: WYMVGU 975 on both arms (four offices each shown 2000-present; the source dates the board seat and the last office only, WYMVGU-02, still open on #1445) and WYMVGU 865 on dev-248 (an undated Past-President shown with the board term, the other half of WYMVGU-02). On the fresh render 865 renders no date (#1449's `_unsourced_date_records`), so the render check keeps it quiet there; read against stage 4 alone it would fire. Before the render check and the prose and inside-the-range rules, the shared-range test fired on 5 entries of the 216 split entries on the three farms: the 2 above, JBUVYV 346 (three years over four talks, every record sharing; no record of its own), JBUVYV 382 (the odd record's start year is the shared range's end) and CAGLNY 13 (a sentence: "In 2016 ... I was awarded" three grants). Those three are the #1458 trap shape, one range given to every record, and the shipped rule spares all three (CAGLNY 13 by the prose test, which only matters without the docx: its grant tables have no row the render check finds). The trap the rule cannot separate is "2015 talk A, talk B, 2016 talk C": one year printed over two talks, which #1449 treats as sourced. No split entry on these farms or on the 56 `analysis/` runs whose stage 4 carries `stage4_records` has that layout. n=3: INFO.

Recall on the two EOAHMI recheck findings this lint is for: DUTAVD-03 (entry 30) and WYMVGU-02 (975 by index; 865 is in its description), both on dev-248, where no other lint names those entries. Matched by index: DUTAVD 30 and WYMVGU 975; 865 is caught but not matched. Both shapes were written from these runs, so the precision is in-sample, and n is 4. Neither shape changes the quality score: like `span_count` and `role_consistency`, the lint is not a `quality_score.py` input.

JUNK-EV changes only which row `junk_or_header_row` quotes as evidence: of the rows that match an entry, it quotes the first whose year agrees with the entry's (a year in its text or a date field), else the first. A finding is still emitted exactly when some row matches. Base and branch were run over the same renders, with a second base run as a determinism control (0 runs differ on every corpus). On all 294 runs no finding was added or removed, and no severity, message, other lint or metric changed (findings: 3,394 on the 105 `analysis` runs, 2,146 on the farm, 2,267 on the 126 runs; `junk_or_header_row` 198, 108 and 36 of them). 16 evidence lists changed, each read by hand, and each moved from a dated record to the entry's own row: QTATUP 1252, 1253 and 1261 and KHXOUF 1252 and 1261 (a society or PRN header's own `<organization> | Member` row, not the dated membership or office row before it), BNYLDF 620, GKAQHB 116, RWBQKF 116, IZJADE 491 and WYMVGU 491 (the same shape), BZZNRL 231 and 274 (an undated K4 label's own row, on both the `analysis` run and the farm) and CXRYCF 110 (an undated division header's own row, on both). None changed on the 126 runs. Verdicts that move: the EOAHMI recheck read QTATUP's 13 hits as 10 true and 3 (1252, 1253, 1261) real junk rows quoting a valid neighbouring row, so 13 of 13 now quote the row they flag; NDMRSO's KHXOUF review counted 1252 and 1261 short for quoting the valid membership rows, though the rows they name are real junk (KHXOUF's verified Q2 1252/1253/1261 finding), so KHXOUF is 14 of 15, the 15th (1286) an entry stage 6 dropped. UYQRUN 157 and 176 do not move: those entries render no row at all, and the row each quotes is another record's, which is a different defect (the lint should not fire), not a choice between two matching rows. Counts are unchanged, so `junk_or_header_rows` in `quality_score.py` (the #1443 cap input) is unchanged on every run.

The EBYSBC verdicts were given on the doctor deployed for that batch (dev-242, `f4f087fc`). On M1's code every judged (run, lint) pair still fires, so the verdict columns describe the same findings as the hit columns.

## Per-lint precision

| lint | hits | warn+ | judged TP / partial / FP | TP / judged | matched / hits | caught | measured |
|---|---|---|---|---|---|---|---|
| `classified_unrendered` | 1 | 0 | 0 / 0 / 1 | 0 / 1 (0%) | names text | 0 | M1 |
| `contact_slot_lost` | 1 | 1 | 1 / 0 / 0 | 2 / 2 hand-checked (100%) | 1 / 1 (100%) | 1 | RCB-D |
| `date_only_lines` | 2 | 0 | none | none | names text | 0 | M1 |
| `dedup_drops` | 14 | 7 | 2 / 2 / 4 | 2 / 8 (25%) | 3 / 14 (21%) | 3 | M1-dup |
| `duplicate_passages` | 1 | 1 | none | none | names text | 0 | M1 |
| `duplicate_records` | 13 | 13 | 4 / 0 / 1 | 6 / 7 judged or hand-checked (86%) | 1 / 9 (11%) | 1 | RCB-D |
| `enrichment_failures` | 15 | 15 | 3 / 4 / 0 | 3 / 7 (43%) | names text | 0 | M1, RCB-D |
| `enrichment_pubtype_mismatch` | 2 | 2 | 2 / 0 / 0 | 2 / 2 (100%) | 1 / 2 (50%) | 1 | M2-enrich |
| `grant_boundary` | 45 | 45 | none | 44 / 45 hand-checked or matched (98%) | 27 / 45 (60%) | 11 | W3B-GR |
| `grant_bucket` | 16 | 16 | none | 16 / 16 matched (100%) | 16 / 16 (100%) | 2 | W3B-GR |
| `implausible_year` | 21 | 21 | 1 / 0 / 1 | 1 / 2 (50%) | 7 / 21 (33%) | 5 | M1 |
| `junk_or_header_row` | 107 | 107 | 102 / 2 / 0 | 105 / 107 hand-checked (98%) | 68 / 107 (64%) | 20 | RCB-D |
| `llm_fallback_served` | 1 | 1 | 1 / 0 / 0 | 1 / 1 (100%) | names text | 0 | M1 |
| `missed_headers` | 21 | 0 | 4 / 2 / 2 | 4 / 8 (50%) | names text | 0 | M1 |
| `multi_record_coverage` | 152 | 103 | none | 12 / 13 hand-checked (92%) | 85 / 152 (56%) | 48 | RCB-D |
| `offschema_fields` | 47 | 14 | 16 / 2 / 5 | 16 / 23 (70%) | 34 / 47 (72%) | 28 | M1 |
| `output_hygiene` | 55 | 0 | 30 / 3 / 0 | 30 / 33 (91%) | names text | 0 | M1 |
| `pipe_leaks` | 5 | 5 | 0 / 0 / 2 | 0 / 2 (0%) | names text | 0 | M1 |
| `pubmed_title_truncated` | 22 | 22 | 22 / 0 / 0 | 22 / 22 (100%) | 8 / 22 (36%) | 2 | M2-enrich |
| `role_consistency`: `contradicted` | 15 | 15 | none | 15 / 15 hand-checked (100%) | 2 / 15 (13%) | 1 | RC-ROLE |
| `role_consistency`: `pi_cell_empty` | 159 | 159 | none | 157 / 159 hand-checked or script-checked (99%) | 11 / 159 (7%) | 1 | RC-ROLE2 |
| `role_consistency`: `pi_also_co_i` | 7 | 0 | none | 7 / 7 hand-checked (100%) | 0 / 7 (0%) | 0 | RC-ROLE2 |
| `role_consistency`: `pi_from_collaborator` | 5 | 0 | none | 5 / 5 hand-checked (100%) | 4 / 5 (80%) | 1 | RC-ROLE2 |
| `role_consistency`: `owner_lead_as_co_i` | 5 | 0 | none | 5 / 5 hand-checked (100%) | 3 / 5 (60%) | 1 | RC-ROLE2 |
| `section_consistency` | 28 | 28 | none | 30 / 30 hand-checked (100%) | 14 / 28 (50%) | 14 | RCB-D, RCB-HE |
| `section_lost` | 1 | 1 | 0 / 1 / 0 | 0 / 1 (0%) | names text | 0 | M1 |
| `segmentation_collapse` | 1 | 1 | none | 1 / 1 hand-checked (100%) | 1 / 1 (100%) | 1 | W3B-SC |
| `span_count` | 118 | 118 | none | 111 / 118 hand-checked (94%) | 4 / 118 (3%) | 2 | RCB-SC |
| `split_child_unsourced`: `institution_from_outside_entry` | 1 | 0 | none | 1 / 1 hand-checked (100%) | 1 / 1 (100%) | 1 | SC-1 |
| `split_child_unsourced`: `date_from_sibling` | 3 | 0 | none | 3 / 3 hand-checked (100%) | 2 / 3 (67%) | 1 | SC-1 |
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
| `wrong_start_date` | 5 | 1 | 0 / 1 / 0 | 0 / 1 (0%) | 5 / 5 (100%) | 5 | RCB-D |

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
| `junk_or_header_row` | 5 or more on a run | 151 / 165 (92%): farm 102 / 104 (M2-junk), RCBKFG 9 / 9, NDMRSO 40 / 52 (77%; 38 / 52 before JUNK-EV) | 102 / 105 (97%): farm 69 hits on 6 runs, the 2 M2-junk partials counted as if they sit there; NDMRSO KHXOUF 14 / 15 (12 / 15 before JUNK-EV), BNYLDF 11 / 11, SYWZJA 5 / 5, GCFEBE 5 / 5 | yes |
| `etal_added` (INFO) | | RCBKFG 92 / 92; NDMRSO 17 judged lines, 0 false | | no: see below |

`grant_boundary`'s two false positives (RXYBVF 502, VYNARH 648) sit on runs with 1 and 2 hits, and a lone hit is one grant's edge; a slipped stage-2 cut carries down the list, which is what 3 or more catches. The NDMRSO 10 / 11 is DXAGUS 6 (judged TP) and VYRDHN 4 (each names a VYRDHN-02 record), against VYNARH 648.

`junk_or_header_row` out of sample is the weakest number here: NDMRSO judged 22 TP, 3 partial and 11 FP over its reviewed runs (EHGXAL 3 of 3 false: trainee headings that do group their awards; UYQRUN 2 of 3 quote a row that is not the entry's own, KHXOUF 3 of 15 quote another row or name an entry stage 6 dropped, of which JUNK-EV fixes the 2 that quoted another row (1252, 1261); REOYVH and SVYSGY 1 of 1). BNYLDF's 11 and GCFEBE's 5 were not in that review and were read for this row: BNYLDF 9 are BNYLDF-03 and 2 are military-award lead-in labels printed as rows; GCFEBE's 5 are GCFEBE-06. Every NDMRSO false positive sits on a run with 1 to 3 hits, so the cap needs 5.

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
| 2026-10-05 | `etal_added` | 92 of 92 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `grant_bucket` | 16 of 16 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `grant_boundary` | 14 of 14 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `multi_record_coverage` | 9 of 10; silent on all 4 CAOACN multi-record losses (two-digit and M/YY dates); RCB-D catches 2 of them | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `stage6_render_warnings` | 8 of 10 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `junk_or_header_row` | 9 of 9 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `offschema_fields` | 6 of 9 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `output_hygiene` | 4 of 5, 1 a neutral metric | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `missed_headers` | 3 of 3 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `enrichment_failures` | 1 of 3; the JJUQDF shared-PMID false positive is fixed in RCB-D | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `table_shape` | 2 of 3 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `wrong_start_date` | 0 of 3 (FLYBMX grants); fixed in RCB-D | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `section_consistency` | 1 of 2 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `taxonomy_code_coverage` | 1 of 1 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `contact_slot_lost` | 0 of 1 (CAOACN home number); fixed in RCB-D | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `year_not_in_source` | 0 of 1 (CAOACN 454, an 'M/DYY' date) | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | `teaching_postcheck` | 1 of 1 | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
| 2026-10-05 | RCB-D's new shapes (`multi_record_coverage` two-digit dates, `junk_or_header_row` `role_only`, `duplicate_records` one letter apart, `section_consistency` reviewer journals) | 3 of 13 out of sample; the 10 false are fixed in RCB-D2 | 126 farm and batch runs, render of origin/dev `10f18e34` | RCB-D2 |
| 2026-10-05 | all lints | 167 of 182 judged (92%); 75 of 90 without etal_added; recall 33 of 77 verified defects (43%) | RCBKFG, 6 CVs, dev-247 `2fa03115` | RCBKFG batch autopsy (local, not committed), its Doctor accuracy table |
