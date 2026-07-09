# Corpus doctor sweep — results & accuracy backlog (2026-07-09)

Executes the plan in `HANDOFF-doctor-sweep-accuracy-2026-07-09.md`: ran `run_doctor`
(current dev, post-#230 lints) across one representative run per **distinct CV**,
then adversarially verified every top finding against the raw output docx to
strip heuristic false positives. The verified ranking below **is** the accuracy
backlog.

## Method (reproducible)

- Corpus: `s3://wcm-cviche-storage/cviche/runs/` — 121 run dirs, 93 with a
  `fields.json`. Collapsed with `scripts/corpus_distinct_cvs.py`: **93 runs → 12
  distinct owners (21 by content-hash)**; one CV (Bennett) is 47% of runs. Quote
  distinct CVs, never runs.
- Reps: the **most-recent run per distinct owner** (12 reps) — freshest artifact
  per CV. `scripts/corpus_doctor_sweep.py` stages each flat S3 `outputs/` dir into
  the `stage_*/` layout the doctor expects and runs all 13 lints.
- Verification: 4-agent workflow, one per top class, each grepping the flattened
  docx (paragraphs **+** table cells) and stage JSON to label each finding
  REAL / PARTIAL / FALSE_POSITIVE with a cited reason.

### Two coverage gaps (server artifacts, not the doctor)
- **No source docx on S3** (only `outputs/` + `prompt_logs/`), so `segmentation`
  and `missed_headers` (lints 1–2, the top-severity ones) **could not run**.
  `under_extraction` catches the same fused-entry class from stage_4 alone.
- **`_render_warnings.json` (#228 sidecar) is absent from every run**, including
  the July ones, so `dedup_drops` and `stage6_render_warnings` **never ran**. The
  sidecar isn't landing in S3 outputs — worth a ticket on its own.

### The vintage caveat that reframes the ranking
**#243 (layout-table explode) merged to dev today; all 12 rep artifacts predate
it** (newest run 2026-07-08). So this sweep bounds **pre-#243** behavior. The
classes whose defects come from run-together blobs / 1-cell layout tables
(`table_shape`, the cell-split slice of `classified_unrendered`) may already be
partly fixed on current code — re-run the top CVs post-#243 before building.
**#248 (pipe-fusion) is explicitly the #243 follow-up and is _not_ fixed by it**,
so the `fusion_cluster` findings stand.

## Raw vs verified

Distinct CVs (of 12) with ≥1 finding, before and after verification:

| lint | raw | **verified real** | notes |
|---|---:|---:|---|
| classified_unrendered | 9 | **5** | 8/18 findings real; halved by FP |
| table_shape (#229) | 7 | **5** | 4 REAL + 1 PARTIAL; 2 FP |
| output_hygiene | 8 | **2** | only the 2 ERROR code-leaks are real |
| pipe_leaks (#248) | 3 | **3** | |
| under_extraction (#248) | 2 | **2** | } same 3 CVs — 16/19 findings real |
| unrendered_records (#248) | 2 | **2** | |
| enrichment_failures (#222) | 2 | **0** | citations render fine from CV fields |
| dead_sections | 1 | **1** | leadership section misrouted |
| bucket_status | 0 | 0 | ran clean on all 12 |
| segmentation / missed_headers | – | – | no source docx (not run) |
| stage6_render_warnings / dedup_drops | – | – | no #228 sidecar (not run) |

## The accuracy backlog (verified, ranked by broad × real × severity)

**1. Research-summary narrative silently dropped (M1) — NEW, no ticket — 3/12 CVs.**
The M1 research statement/narrative is replaced in the output by the bare WCM
template instruction `Research Activities:`. Confirmed lost on Vasquez (2BXUXG),
Kim (B2RRRA), Arbini (CQXGKG) — distinctive phrases (`pathological myocardial
remodeling`, `culturally competent`, `Ilya Kister`) appear nowhere in the docx.
Broadest **new** real bug; clean fix (route the M1 narrative to the research
section instead of the template prompt). Almost certainly not touched by #243.

**2. Honors/awards table mis-shape (#229) — 5/12 CVs (4 REAL + 1 PARTIAL).**
Real: run-together name-cell blobs, whole-table duplication with names leaking
into the date column (EH4XXA), dropped date columns + a whole missing award
(9TUVGW loses FIDSA + both date ranges). PARTIAL: P2ZP1A repeats the org in both
name and org columns (redundant, no loss). 2 FP where the org is legitimately
part of the award name (`ASCO Foundation Merit Award`). Already ticketed; some of
the blob slice may improve post-#243.

**3. Pipe-delimited multi-record fusion (#248) — 3/12 CVs, highest per-CV severity.**
The WCM-template CVs (Jung/Miller/Borys) with `Award Source: … | Project title:`
grant tables. 16/19 findings real, two faculty-visible failure modes: (a) raw
` | ` cells + template scaffolding (`Award Source:`, `Duplicate table below as
needed`) dumped verbatim into the output; (b) genuine record loss — EH4XXA drops
both training grants and 2/3 mentee records that exist in stage-4 JSON; 9TUVGW
drops the "major goals" narrative of 4 grants that survive only as raw pipe rows.
Low FP (~11%). Ticketed, **unblocked by #243**, deterministic fix on #243's
explode machinery. Narrow but catastrophic where it hits.

**4. Taxonomy-code leak in output text (`• [M2B]`, `• [D1]`) — 2/12 CVs, ERROR.**
Raw bracketed taxonomy codes render on grant/pub/appointment bullets (9TUVGW ×22,
EH4XXA ×2) — plainly visible to a reader. Cheap render-time strip. NEW-ish
(output-hygiene ERROR); file if no ticket covers it.

**5. Bibliography mid-range truncation — NEW — 2/12 CVs.**
Arbini (CQXGKG) renders only bibliography items 8/48/49 — ~all mid-range pubs
(19,23,25,33,34,36,44,47) absent. Kim (B2RRRA) loses one pub. Distinct from the
fusion class; needs its own look at the citation render path.

**6. Leadership section misrouted (tax 'O') — 1/12 CV.**
9TUVGW: 3 real leadership positions present in classified/fields/enriched JSON but
absent from the output; the section renders only its template prompt.

### Non-defects (do not chase)
- **`enrichment_failures` (#222): 0/2 real.** `doi_found_but_fetch_failed`
  citations still render complete from CV-extracted fields — no visible
  degradation. The lint over-warns; consider downgrading to INFO or gating it on
  an actual render check.
- **`output_hygiene` appendix WARNs: 0/8 real.** Every "N unmapped entries" /
  "boilerplate in appendix" is by-design catch-all parking (`CURRICULUM VITAE`
  title echoes, WCM template prompts, empty `| |` cells) — content is visibly
  parked, not lost. A boilerplate-strip would be a nicety, not an accuracy fix.

## Doctor-tool follow-ups (bonus, cheap)
- `classified_unrendered` should pipe-normalize/split the run-together stage-3b
  blob and skip empty-template-header entries before the containment check —
  would ~halve its FP rate (10 of 18 findings here were FP).
- File: `_render_warnings.json` sidecar not written to S3 outputs → 2 lints dark
  server-side. And/or reconstruct source lines from `prompt_logs/` to re-enable
  `segmentation`/`missed_headers` on server artifacts.

## Artifacts
- `scripts/corpus_doctor_sweep.py` (new, this branch) — stage + sweep + rank.
- `scripts/corpus_distinct_cvs.py` (PR #246) — distinct-CV collapse.
- Rep map: PZ69YW=Bennett, 2BXUXG=Vasquez, OAFDGA=Rahman, HNFLBA=Jung,
  IYRUBQ=Donahue, B2RRRA=Kim, EHMQSQ=Tang, P2ZP1A=⟨blank⟩, 9TUVGW=Miller,
  EH4XXA=Borys, CQXGKG=Arbini, I5NKUG=Herr.
