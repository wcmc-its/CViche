# Corpus doctor sweep — results & accuracy backlog (2026-07-09)

Executes the plan in `HANDOFF-doctor-sweep-accuracy-2026-07-09.md`: ran `run_doctor`
(current dev, post-#230 lints) across one representative run per **distinct CV**,
then adversarially verified every top finding against the raw output docx to
strip heuristic false positives. The verified ranking below **is** the accuracy
backlog.

> **CORRECTION (2026-07-09, post-verification).** A second verification pass found
> that the first pass — and the doctor's own docx readers — were **blind to
> tracked-change `<w:ins>` content**. Stage 6 inserts LLM-enriched content
> (research summaries, reformatted citations) as tracked *insertions*, which
> python-docx's `.text` skips. So two "top" findings were **false positives**: the
> "M1 research-summary drop" (was ranked #1 new bug) and the "bibliography
> truncation" — both render fine as tracked insertions. Fixed the reader in
> **#249 / PR #255** (`_docx_text` reads `<w:ins>`, excludes `<w:delText>`);
> **#250 closed** as not-a-bug. The tables and backlog below are **corrected** to
> the track-change-aware counts. Net: `classified_unrendered` genuine-loss CVs
> dropped from 5 to **2**; the real backlog is the #248 fusion cluster, #229
> honors tables, the #251 code-leak (PR #254), and two narrow residual losses.

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

### Coverage
- **Source docx IS retained** at `runs/<id>/input/<uid>.docx` (durably archived on
  every upload since 2026-06-02, commit 8358c0b). Staging it lets the top-severity
  `segmentation` + `missed_headers` lints run: they fire on **5/12** and **6/12**
  CVs respectively (ran on the 9 reps with source; the 3 oldest runs predate the
  archiving feature). These need the same track-change-aware verification before
  they're trusted. (An earlier draft wrongly said "no source docx on S3" — that
  was an artifact of a `--include '*/outputs/*'` sync filter, not reality.)
- **`_render_warnings.json` (#228 sidecar) is absent from every run**, including
  the July ones, so `dedup_drops` and `stage6_render_warnings` **never ran** — the
  sidecar isn't landing in S3 outputs (#252).

### The vintage caveat that reframes the ranking
**#243 (layout-table explode) merged to dev today; all 12 rep artifacts predate
it** (newest run 2026-07-08). So this sweep bounds **pre-#243** behavior. The
classes whose defects come from run-together blobs / 1-cell layout tables
(`table_shape`, the cell-split slice of `classified_unrendered`) may already be
partly fixed on current code — and because the **source docx is retained**
(`runs/<id>/input/`), the top CVs **can be re-run** post-#243 to confirm before
building (no need to wait for a fresh user upload).
**#248 (pipe-fusion) is explicitly the #243 follow-up and is _not_ fixed by it**,
so the `fusion_cluster` findings stand.

## Raw vs verified

Distinct CVs (of 12) with ≥1 finding, before and after verification:

Distinct CVs (of 12) with ≥1 finding: raw lint → first verification →
**track-change-aware** (the trustworthy column):

| lint | raw | verify-1 | **corrected** | notes |
|---|---:|---:|---:|---|
| classified_unrendered | 9 | 5 | **2** | M1 + reformatted-citation "losses" were `<w:ins>` FPs; real = Bennett mentee-counts, Miller invention |
| table_shape (#229) | 7 | 5 | **5** | tables aren't track-changed; 4 REAL + 1 PARTIAL, 2 FP |
| output_hygiene | 8 | 2 | **2** | only the 2 ERROR code-leaks (#251) |
| pipe_leaks (#248) | 3 | 3 | **3** | raw pipes visible plain-text — real |
| under_extraction (#248) | 2 | 2 | **2** | same 3 fusion CVs |
| unrendered_records (#248) | 2 | 2 | **2** | major-goals / mentee / grant genuinely absent |
| enrichment_failures (#222) | 2 | 0 | **0** | citations render fine from CV fields |
| dead_sections | 1 | 1 | **1** | leadership misrouted (absent even w/ track-changes) |
| bucket_status | 0 | 0 | 0 | ran clean on all 12 |
| segmentation | – | – | **5** | ran on 9 (source staged); UNVERIFIED |
| missed_headers | – | – | **6** | ran on 9 (source staged); UNVERIFIED |
| stage6_render_warnings / dedup_drops | – | – | – | no #228 sidecar (not run) |

The `<w:ins>` blind spot inflated only the "content-absent" lints
(`classified_unrendered`, `unrendered_records`, `dead_sections`); it does **not**
touch `table_shape` (reads table cells, not track-changed) or `pipe_leaks` (the
raw pipes are visible plain text). Fixed in #249 / PR #255.

## The accuracy backlog (verified, ranked by broad × real × severity)

**1. Pipe-delimited multi-record fusion (#248) — 3/12 CVs, highest per-CV severity.**
Now the top real bug. The WCM-template CVs (Jung/Miller/Borys) with
`Award Source: … | Project title:` grant tables. Two faculty-visible failure
modes: (a) raw ` | ` cells + template scaffolding (`Award Source:`, `Duplicate
table below as needed`) dumped verbatim into the output (visible plain text — not
a track-change artifact); (b) genuine record loss — EH4XXA drops both training
grants and 2/3 mentee records that exist in stage-4 JSON; 9TUVGW drops the "major
goals" narrative of 4 grants that survive only as raw pipe rows (absent even with
track-changes). Ticketed, **unblocked by #243**, deterministic fix on #243's
explode machinery. Narrow but catastrophic where it hits.

**2. Honors/awards table mis-shape (#229) — 5/12 CVs (4 REAL + 1 PARTIAL).**
Real: run-together name-cell blobs, whole-table duplication with names leaking
into the date column (EH4XXA), dropped date columns + a whole missing award
(9TUVGW loses FIDSA + both date ranges). PARTIAL: P2ZP1A repeats the org in both
name and org columns (redundant, no loss). 2 FP where the org is legitimately
part of the award name (`ASCO Foundation Merit Award`). Already ticketed; some of
the blob slice may improve post-#243.

**3. Taxonomy-code leak in output text (`• [M2B]`, `• [D1]`) — 2/12 CVs, ERROR — SHIPPED.**
Raw bracketed taxonomy codes render on grant/pub/appointment bullets (9TUVGW ×22,
EH4XXA ×2) — plainly visible (not a track-change artifact). Cheap render-time
strip; **fixed in #251 / PR #254** (`_strip_taxonomy_code`).

**4. Two narrow residual `classified_unrendered` losses — 2/12 CVs.**
Genuinely absent even with track-changes: Bennett (PZ69YW) mentee-supervision
count totals + outcome narrative (`Current Ph.D. Students: 12`, `Ph.D.
Graduated: 38`), and Miller (9TUVGW) a named invention (`Biotia-HSS … Orthopedic
Assay`, tax M4C). Narrow; the Miller case rides with the #248 template-form CV.

**5. Leadership section misrouted (tax 'O') — 1/12 CV.**
9TUVGW: 3 real leadership positions present in classified/fields/enriched JSON but
absent from the output; the section renders only its template prompt.

### Non-defects (do not chase)
- **M1 research-summary "drop" & bibliography "truncation": FALSE POSITIVES.**
  Both render as tracked-change `<w:ins>` insertions the flattened reader missed
  (#249 reader fix / PR #255; #250 closed). No content lost.
- **`enrichment_failures` (#222): 0/2 real.** `doi_found_but_fetch_failed`
  citations still render complete from CV-extracted fields — no visible
  degradation. The lint over-warns; consider downgrading to INFO or gating it on
  an actual render check.
- **`output_hygiene` appendix WARNs: 0/8 real.** Every "N unmapped entries" /
  "boilerplate in appendix" is by-design catch-all parking (`CURRICULUM VITAE`
  title echoes, WCM template prompts, empty `| |` cells) — content is visibly
  parked, not lost. A boilerplate-strip would be a nicety, not an accuracy fix.

## Doctor-tool follow-ups
- **SHIPPED (#249 / PR #255):** the docx readers now read tracked-change `<w:ins>`
  content — the single biggest FP source. Without it the sweep over-reported
  every "content-absent" lint.
- **Still open:** `classified_unrendered` should also pipe-normalize/split the
  run-together stage-3b blob and skip empty-template-header entries before the
  containment check — that's the *remaining* FP mechanism (cell-split rendering,
  e.g. Rahman K1, Kim N3A/N3B), separate from track-changes.
- File (#252): `_render_warnings.json` sidecar not written to S3 outputs → 2 lints
  dark server-side. (The `segmentation`/`missed_headers` gap is NOT a coverage
  problem — the source docx is retained at `runs/<id>/input/`; stage it and they
  run.)

## Artifacts
- `scripts/corpus_doctor_sweep.py` (new, this branch) — stage + sweep + rank.
- `scripts/corpus_distinct_cvs.py` (PR #246) — distinct-CV collapse.
- Rep map: PZ69YW=Bennett, 2BXUXG=Vasquez, OAFDGA=Rahman, HNFLBA=Jung,
  IYRUBQ=Donahue, B2RRRA=Kim, EHMQSQ=Tang, P2ZP1A=⟨blank⟩, 9TUVGW=Miller,
  EH4XXA=Borys, CQXGKG=Arbini, I5NKUG=Herr.
