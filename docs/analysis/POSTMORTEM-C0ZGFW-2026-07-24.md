# Post-mortem — C0ZGFW: a well-structured CV that the pipeline handled badly

Date: 2026-07-24. Subject: run `C0ZGFW`, uploaded 2026-07-21. The source docx is a
real CV and is not committed; it lives at `s3://wcm-cviche-storage/cviche/runs/C0ZGFW/input/`.
Audience: anyone picking up CViche accuracy work. No prior context assumed.

## Summary

C0ZGFW is close to a best case for this pipeline. The source CV is *well organised*:
19 tables, 248 rows, mostly regular 2-3 column shapes with explicit headers
(`Title | Institution/Location | Dates`). Each row is one record. There is very
little free prose to interpret.

The pipeline still produced a document that scores **RED — "re-run / do-not-deliver"**
on its own advisory scorer, in every version tested: 58/100 originally, 59 after
three merged fixes, 52 after two more. Content was lost, then duplicated, then
partly recovered but misfiled.

The interesting finding is not any single bug. It is that **five defects in
different stages compounded, and two of them were actively hiding the others.**
Work aimed at the visible symptom repeatedly landed one or two layers below or
above the actual cause.

## What the failure looked like

Three versions of the output exist, all from the same source file:

| | BAD (original) | RERUN-1 (after #316/#320/#321) | RERUN-2 (after #418/#420) |
|---|---|---|---|
| Total blocks | 982 | 1,136 | 934 |
| Redundant copies | 146 | **268** | **96** |
| Dedup drops | 40 (38 pathological) | 3 | 6 |
| doctor findings | 6 WARN / 1 INFO | 6 WARN / 1 INFO | **3 WARN / 0 INFO** |
| Quality score | 58 RED | 59 RED | **52 RED** |
| INVITATIONS TO SPEAK | 17 blocks | 17 | 18 |

Read that table carefully, because it is the whole story in miniature. The
document got structurally better and *scored worse*. Content loss became content
duplication became content misfiling. At no point did the headline verdict move
off RED.

## Root causes

Five distinct defects, in pipeline order. The first two were only found on
2026-07-24; they had been silently corrupting every CV with tables.

### 1. Row indices round-tripped through JSON numbers (issue #420, fixed)

Stage 2 presents each table row to the model as `parent.row` and looks the answer
back up by string key. The model replies in JSON, where those indices are
**numbers**:

```python
json.loads('{"element_idx_start": 114.10}')   # -> 114.1
str(114.10)                                   # -> '114.1'   lands on row 1
```

Every table row whose index ends in a zero was therefore **unreachable**. Row 10
resolved to row 1, row 20 to row 2, and so on.

Measured on C0ZGFW: element 114 lost rows 10/20/30/40/50; element 109 lost row 10;
elements 31/35 lost rows 10/20. Element 72 (MENTORING, 12 rows) produced **no row
entries at all**.

Corpus-wide: **195 rows across 9 of 21 distinct CVs.** This was never a C0ZGFW
problem; it is a floor on every CV with a table longer than ten rows.

### 2. The whole-table entry survived next to its own rows (issue #418, fixed)

`remove_subset_delimiters` exists to collapse a composite entry when its sub-parts
are also present. It normalises a bare parent index `109` to the **point**
`(109, 0)`. A row at `(109, 4)` therefore fails the `end <= kept_end` containment
test, and both survive. The table is emitted, and rendered, twice.

The blob's origin is mundane: the model labels the header row with the bare
parent index (`{"element_idx_start": 114, "reasoning": "Header row"}`) and the code
resolves that bare index to the *entire table's* text — 12,752 characters.

### 3. Dedup dropped the granular records instead of the blob (issue #227)

Given a 12,752-char blob that textually contains 35 individual records, dedup
dropped the 35 records as "contained" and kept the blob. On the original run that
is 35 real invited presentations reduced to one garbled row.

**This is the defect that made everything else hard to see**, because of a
second-order effect: since defect 1 meant some records existed *only* inside the
blob, the blob was genuinely load-bearing. Dropping it would have lost content.
Dedup was making a bad choice between two bad options, and no threshold tuning
could have fixed that.

### 4. Stage 3b codes invited presentations as teaching (open, unresolved)

Stage 2 produced roughly 70 row entries under the `National` invited-presentations
heading. Stage 3b classified **R = 10**. The rest were coded to K (teaching) and
rendered as bullets in EDUCATIONAL CONTRIBUTIONS.

This is why INVITATIONS TO SPEAK has 12 data rows and `Regional*` has zero, across
all three runs, and it is **not an extraction problem** — the rows existed the
whole time. It is the single largest remaining visible defect.

### 5. Stage 4.5 paraphrases named programs out of the research summary (partly resolved)

The M1 entry "The Program for Medical Education Innovations and Research: Merrin
Physical Diagnosis" is consumed by the Stage 4.5 summary generator, which produces
synthesised prose. `original_m1_content` retains the program name; the rendered
summary text does not. PR #321 (issue #317) routes M1 entries to the appendix when
the summary is *absent* — an explicit no-op here, because the summary rendered
fine. It just didn't mention the program.

This one resolved incidentally: once #420 recovered the grant table's rows
(`Annual direct costs: | $25,000`, `Duration of support:`), the entry had the
structured fields the grant renderer needed and now appears under RESEARCH — twice.
The underlying paraphrase behaviour is unchanged and will bite again.

## Why it went badly

The bug list above is not the lesson. These four dynamics are.

### Compensating layers hid the defect

Defect 1 pushed content into the blob. Defect 2 kept the blob alive. Defect 3 then
had to choose between the blob and the rows, and chose wrong. Each layer was
partially compensating for the one before it, so the *symptom* surfaced two stages
downstream of the *cause*, in stage 6 render output.

Consequence: three separate investigations (#208, #227, #248) all examined render
and dedup behaviour, and all missed a `str(float)` bug in stage 2.

### Fixes landed at the wrong layer, and one made things worse

PR #320 fixed dedup precedence so granular records stop being dropped. It was
correct in isolation, corpus-gated, and it *increased* duplication by 122 rendered
lines on this CV — because the blob it declined to prefer was still being emitted.
Fixing the arbitration between two artefacts, when one of them shouldn't exist, is
motion without progress.

The generalisable rule: **when a fix has to choose which of two representations of
the same content to keep, that is a signal the duplication upstream is the bug.**

### The measurement instruments were blind to the actual damage

This is the most costly dynamic, because it means nobody could have noticed.

- `run_doctor` has **no duplication lint**. RERUN-1 had 122 extra duplicate rendered
  lines and the doctor's verdict was unchanged at 6 WARN / 1 INFO.
- The quality score has a "Duplicate-entry ratio" dimension, but it measures
  duplicate *entries at stage 3b*, not duplicate *rendered output*. It moved 0.079
  to 0.082 while the document visibly doubled sections.
- The closest signal, `echo_paragraphs`, sat at 65 -> 68 on a dimension already
  floored at 0/10 — so a real regression was invisible inside an already-failing
  metric.

A run can get materially worse and score identically. That is a tooling defect at
least as important as any pipeline bug.

### Stochasticity and reformatting make naive comparison useless

Stage 3b classification varies between rolls on identical input (278 vs 268 vs 324
entries across three runs of the same file). Stages 5c/5d reformat teaching entries
and citations, so the same publication appears as `robert nathanson, gregory mints...`
in one run and `Nathanson R, Mints G...` in another.

During this investigation a naive string diff reported "151 records lost" in
RERUN-2. All of them were reformatting or renumbering artefacts. Any comparison
method that does not neutralise 5c/5d and list renumbering will manufacture false
content-loss findings — and has done so before on this project.

### Issue framing sent people to the wrong code

Issue #208 is titled "stop fusing multi-row tables in stage 2." **Nothing fuses.**
The reader returns all 19 tables with full row data; the splitter produces correct
per-row entries. Anyone following that title searches the splitter and finds
nothing wrong — which is consistent with #208 having stayed open a long time.

## Current state

Shipped and merged before this work: #316 (segmentation crash), #320 (#227 dedup
precedence), #321 (#317 M1 appendix fallback).

Open in PR #419 (review only, not merged): #418 and #420, two commits, 367 tests,
CI green.

Net effect on C0ZGFW: duplication solved and better than the original (146 -> 96
redundant copies), doctor findings halved (6 WARN -> 3 WARN, segmentation and
under-extraction lints cleared), 28 rows recovered, BIBLIOGRAPHY 55 -> 80 blocks,
three records rendering that appeared in neither previous version.

And the score fell to 52, because 28 newly surfaced rows plus recovered header rows
now flow into classification for the first time: T catch-all went 8 -> 23,
all-null field extractions 7 -> 15, appendix unmapped entries 8 -> 20.

**The extraction layer is fixed. The content it surfaced is now stressing the
classification layer, which was never carrying this load before.**

## Evidence provenance

Given that this document's argument is partly *that the instruments were unreliable*,
it owes an account of where its own numbers came from.

**Recomputed directly from artifacts** (trustworthy): block and duplicate counts,
computed by walking the docx body XML of all three outputs; section block counts, same
method; doctor findings, by running `scripts/doctor_one.py` at one code version against
all three output sets so the comparison is like-for-like; quality scores, by calling
`quality_score.score_run` on each output set; row-index gaps and stage-2 entry counts,
read from `*_entries.json`; the JSON float collision, reproduced in a standalone
interpreter.

**Reported by the pipeline, not independently verified**: the ~$2.71 per-run cost and
per-stage token/cache figures. These come from the same reporting plumbing that is not
otherwise audited in this document, so treat them as indicative.

**Estimated, with a stated method**: the "195 rows across 9 of 21 distinct CVs" figure
counts collision-shaped gaps below the highest observed row index per parent. It is
deliberately conservative — rows the model skipped outright are not counted — so it is
a floor, not a measurement.

**One claim was circular and has been retracted.** PR #419's original description
reported "0 drops with an uncovered line — no content loss" as a corpus gate result. It
is a tautology: the check computed uncovered lines only for entries that had already
passed the coverage test, and it re-implemented the same predicate the fix uses. It
asked the fix whether the fix was right, using the fix's own definition of "covered."
Corrected on the PR.

The related measurement that *does* hold is the one where the check and the code under
test disagreed: running the **unconditional** drop found 44 of 59 parents carrying
uncovered content. Whether the 13 conditional drops lose anything remains unvalidated
independently — doing that properly means checking dropped parents against the *source
docx* rows rather than against sibling entries.

A note on "deterministic" as a justification, used in #420's commit message: it means
reproducible, not correct. The backstop recovered 5 template header rows every single
run, and the end-to-end rerun shows T catch-all 8 -> 23 and all-null field extractions
7 -> 15. A deterministic rule that misfires misfires every time.

## Options

Grouped by what they address. Effort is rough: S = under a day, M = a few days,
L = a week or more.

### A. Eliminate the index-encoding class outright

The backstop in #420 recovers rows the lookup can't reach. It does not stop them
becoming unreachable.

1. **Emit indices as JSON strings** (`"114.10"`). S. Smallest change; depends on
   model compliance, so it needs a schema constraint to be trustworthy.
2. **Renumber elements to unique integers** before presenting them, mapping back
   internally. S/M. Removes the composite `parent.row` encoding entirely, so no
   separator, no float, no collision. Most robust of the three.
3. **Constrain the response schema** so indices are a typed field the provider
   enforces. M. Bedrock already supports forced tool-use schemas (PR #244).
   Prevents a whole family of malformed-index problems, not just this one.

Recommendation: **none of these on their own — go to option 4 instead.** Option 4
retires this entire group as a side effect, and #420's merged backstop is an
adequate interim guard while 4 is built. Reach for 2 (with 3 as the durable
follow-up) only if scoping 4 shows its effort estimate was optimistic. Decide that
after a day of scoping, not by default.

### B. Stop asking the model to segment tables at all

A table row is a record. That is a structural fact from the docx, not a judgement
call.

4. **Deterministic table entries.** M. One source row becomes one entry by
   construction; the LLM is used for classification and field extraction only.
   Removes defects 1, 2 and 3 simultaneously, since neither blob nor arbitration
   can exist. This is the highest-leverage option in this document.
5. **Keep the model but pass rows as opaque IDs** (`r_114_10`) rather than
   positional indices. S. Cheaper than 4, addresses less.

Recommendation: 4 is the real fix. It also cuts LLM cost — stage 2 currently spends
tokens re-deriving structure the docx already states.

### C. Fix the classification bottleneck (highest visible payoff)

Defect 4 is now the binding constraint on both the invitations section and the
score.

6. **Pin table rows to their section's taxonomy code.** S/M. Stage 3a already maps
   `INVITED PRESENTATIONS -> R` and `Regional/National/International -> R` with high
   confidence, and did so correctly in every run. If a row lives under a confidently
   mapped header, default it to that code and let content override only on strong
   evidence. This inverts the current precedence, which is what lets 60 invited
   presentations become teaching bullets.
7. **Investigate the R-vs-K confusion directly.** S. Same content classifying
   differently under different headers is diagnosable from existing prompt logs at
   zero LLM cost.
8. **Reduce T catch-all use.** M. T went 8 -> 23 as recovered rows arrived; those
   rows are mostly well-formed table records, so a catch-all is the wrong outcome.
9. **Self-consistency for low-confidence rows.** L. Best-of-N with agreement, applied
   only where confidence is low. Expensive; only worth it after 6-8.

Recommendation: 6 first. It is the cheapest change with the largest visible effect,
and it directly targets the one section a reader will notice is empty.

### D. Make the instruments able to see failure

Without this, every future fix is unverifiable — which is how #320 shipped a
regression through a corpus gate.

10. **Duplication lint in run_doctor.** S. Count repeated rendered blocks; warn above
    a threshold. Would have caught PR #320's regression immediately.
11. **Row-coverage lint.** S. For each source table, assert emitted entries >= non-empty
    source rows. Would have caught defect 1 years earlier, and is a two-line invariant.
12. **Score rendered duplication, not entry duplication.** S. The existing dimension
    measures the wrong artefact.
13. **Comparison harness that neutralises 5c/5d and list renumbering.** M. Encode the
    known-good recipe once instead of re-deriving it per investigation, since getting
    it wrong manufactures false findings.
14. **Deterministic replay via cached LLM responses.** M/L. Prompt logs are already
    written per run. Replaying them makes stage-6 changes testable without spend or
    stochasticity, and makes regressions attributable.

Recommendation: 10 and 11 before any further accuracy work. They are hours of effort
and they are what turns "the score didn't move" into a trustworthy statement.

### E. Address the score's dominant penalties

The score cannot leave RED while three dimensions zero out, none of which this work
touched.

15. **Contact/location extraction.** S/M. `0/15`, `inference_success=False`,
    `primary_location` missing. Largest single lever on the number, and likely
    a narrow extraction fix rather than a deep problem.
16. **Sparse table population.** M. `sparse_table_ratio` 0.43-0.48 across all runs.
17. **Raw formatting artefacts.** M. `echo_paragraphs` 65-69, `raw_tab_paragraphs` 3;
    dimension floored at 0/10, so it is invisible to the score until largely fixed.

Recommendation: 15 is worth doing simply because a CV with no contact block cannot
ship regardless of everything else.

### F. Housekeeping

18. **Retitle or close #208.** S. Its premise is disproved; leaving it invites wasted work.
19. **Reassess #320 now that the blob is gone.** S. Its threshold change was justified by
    C0ZGFW's 35 lost records, which no longer occur that way. It may now be doing less
    than it appears while still carrying duplication risk.
20. **De-duplicate the M1 render.** S. The Merrin entry renders twice in RERUN-2.

## Suggested sequence

**0. Merge PR #419.** Everything in "Current state" above lives on an unmerged
branch. None of it is real until #418/#420 land, and once they do they are the new
baseline every subsequent fix is measured against. Do this first or every
measurement below is against a moving target.

1. **10, 11, 12** — duplication lint, row-coverage lint, and score rendered
   duplication rather than entry duplication. Roughly a day for all three. 12 belongs
   in this batch because it is the same blindness in a second instrument, and both
   instruments reported "no change" while the document visibly doubled. Until this
   lands, "the score didn't move" is noise rather than signal.

2. **7 before 6.** 7 is an hour reading prompt logs already on disk, at zero LLM
   cost, and it answers the first open question: *why* does 3b code R-headed rows as
   K? If the logs show a specific cause — row phrasing, header context missing from
   the prompt — that determines how aggressive 6's override should be. Implementing 6
   blind risks over-correcting a problem whose mechanism is unknown.

3. **6** — pin table rows to their section code. Largest visible win; fixes the empty
   invitations section. **Validate across at least two rolls.** 3b produced 278/268/324
   entries on identical input, so a single run can easily manufacture a false win.

4. **15** — contact extraction. Unblocks the score's biggest single penalty, and a CV
   with no contact block cannot ship regardless of anything else.

5. **4** — deterministic table entries. Scope for a day, then commit. This strictly
   dominates group A: it retires defects 1, 2 and 3 as a class because neither the blob
   nor the index round-trip can exist, and it cuts LLM spend. Fall back to 2/3 only if
   scoping shows the effort estimate was wrong.

6. **18, 20** — housekeeping. Then **19 only after step 5**: reassessing #320's
   threshold change makes sense once the blob genuinely cannot exist. Doing it earlier
   re-litigates arbitration between two artefacts that deterministic entries delete
   anyway.

Defer without guilt: **9** (self-consistency — premature until 6-8 are done), **13/14**
(valuable but M/L; do 14 when you next need to bisect a stage-6 regression, not
speculatively), **16/17** (until the three zeroed score dimensions are the binding
constraint rather than classification).

## Things to avoid

- **Do not tune dedup thresholds.** They are load-bearing across every CV and were
  never the cause. The duplication came from an entry that should not have existed.
- **Do not change table indices in `extract_docx_structure`.** It assigns tables a
  *string* idx (`table_N`) that stage 2 depends on via `startswith` and sort keys.
  Fix table-index problems at the consumer boundary. This has bitten before.
- **Do not compare run outputs by naive string diff.** Stages 5c/5d reformat and lists
  renumber; you will report content loss that did not happen.
- **Do not trust a single roll.** Stage 3b varies between runs on identical input.
  Confirm any classification finding across at least two.
- **Do not treat an unchanged doctor verdict as "no regression."** It has no duplication
  lint. See option 10.

## Open questions

- Why does stage 3b code invited presentations as K when stage 3a maps their header to R
  with high confidence? Answerable from existing prompt logs at no cost.
- Are the 5 recovered template header rows (`Title | Institution/Location | Dates`) causing
  the appendix unmapped count to rise 8 -> 20, or is that the recovered content rows?
- Does the row-index collision affect non-table content anywhere else that indices are
  round-tripped through the model?
- Is `sparse_table_ratio` ~0.45 a rendering defect or an accurate reflection of a CV that
  genuinely lacks those fields? It has never been investigated.

## Reproduction

```bash
# source CV and all three output versions
aws s3 cp s3://wcm-cviche-storage/cviche/runs/C0ZGFW/input/C0ZGFW.docx .
aws s3 ls s3://wcm-cviche-storage/cviche/runs/C0ZGFW/ --recursive

# full local run (~35 min, ~$2.71, Bedrock Sonnet 4.6)
python3 run_full_pipeline.py C0ZGFW.docx

# doctor and score on a local run
PYTHONPATH=src python3 scripts/doctor_one.py src/unified_pipeline/outputs C0ZGFW C0ZGFW.docx
PYTHONPATH=src python3 -c "from unified_pipeline.quality_score import score_run; \
    print(score_run('<flat_outputs_dir>', 'C0ZGFW'))"

# corpus for blast-radius gating (read-only, IAM user 'reciter')
aws s3 sync s3://wcm-cviche-storage/cviche/runs/ <dir> \
    --exclude '*' --include '*/outputs/*_entries.json'
```

The S3 corpus is duplicate-heavy — 99 runs are 21 distinct CVs. Always collapse by
CV before quoting prevalence.
