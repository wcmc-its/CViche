# Option 7 — why stage 3b codes invited presentations as teaching (C0ZGFW)

Date: 2026-07-24. Zero-cost read of existing stage-3b prompt logs from the RERUN-2
run (both fixes in tree). No LLM calls. Answers the post-mortem's first open
question before implementing option 6.

## Finding

Of **88 entries** sitting under an `INVITED PRESENTATIONS` section path in stage 3b,
only **8 (9%)** were coded `R`. The rest went to teaching codes — **51 K4**, 10 K2,
3 K1 — plus 7 `T` and a scattering of S8/Q2.

Per subsection:

| Section path passed to 3b | Codes returned |
|---|---|
| `INVITED PRESENTATIONS > National` | K4×48, R×6, T×5, K2×5, S8×3, K1×3, Q2×2, K5×1, S9×1 |
| `INVITED PRESENTATIONS > Regional` | K2×5, K4×3, T×2, R×2, K3×1, S8×1 |

So R = 10 in the rendered doc was not extraction failure and not a lost header. The
rows were present, correctly grouped under the right parent, and the model **read
the header and classified against it anyway**.

## Why — the model is following its instructions

This is not a bug in the model's behaviour. It is doing exactly what the prompt tells
it to. Two forces in the stage-3b system prompt override the section header, and on
this CV they fire on nearly every row.

### 1. Content-over-hierarchy is explicit, and POCUS courses read as teaching

The system prompt (verbatim):

> GUIDELINE: Hierarchy should INFORM your classification but not be DETERMINATIVE.
> - When content clearly fits a single code (e.g., a journal article is S1 regardless
>   of where it appears), that code takes precedence.

The reasoning strings show the model doing precisely this, with high confidence:

- `K4 0.95` — "Explicitly labeled 2-day CME POCUS course for practicing professionals
  with lecturer and hands-on instruction"
- `K4 0.90` — "CME workshop for practicing professionals (SGIM members) with didactic
  and hands-on instruction on POCUS"
- `R 0.90` — "Invited panelist at UT Southwestern grand rounds on career development;
  invited institutional presentation"

The distinction the model draws is real and defensible: a *grand-rounds talk* is a
presentation (R); a *2-day hands-on CME course you ran* is teaching (K4). The trouble
is that a POCUS educator's invited engagements are overwhelmingly the second kind —
they were invited to *teach a course*, and the content honestly reads as teaching.
Content-over-hierarchy is a good rule that this particular CV is adversarial to.

### 2. The header carries no authority into the prompt

The header context is delivered with three separate hedges:

> These are *not* authoritative taxonomy codes.
> SUGGESTED CODES ... are automated first-pass suggestions that may be WRONG ... Use
> them only as weak hints.
> Hierarchy should INFORM ... but not be DETERMINATIVE.

Stage 3a maps `INVITED PRESENTATIONS -> R` and the geographic sub-labels to R with
high confidence, correctly, in every run. **None of that confidence reaches 3b.** By
the time the header arrives it has been reduced to a "weak hint that may be wrong,"
while the entry content arrives as ground truth. The prompt structurally privileges
content over section for every entry, then leans on the model to re-derive intent the
CV author already declared by placing the row under INVITED PRESENTATIONS.

### 3. `T` for short rows compounds the loss

7 of 88 were coded `T` (drop) with reasoning like "Incomplete entry with only 'POCUS
mentor' and no dates or substantive details." Some of these are the recovered rows
whose useful cells were split across the table oddly. So a few invited presentations
are not just misfiled as teaching — they are dropped entirely.

## Implication for option 6

Option 6 (pin table rows to their section's taxonomy code) is the right lever, and
this read sharpens how aggressive it should be:

- **The override must beat high-confidence content classifications, not just break
  ties.** The model returned K4 at 0.90-0.95. A pin that only applies when the model
  is *uncertain* would do nothing here — the model is confidently wrong by design.
  This means inverting precedence for confidently-mapped headers, not merely adding a
  tiebreak.

- **Scope the pin to headers 3a mapped with high confidence.** `INVITED
  PRESENTATIONS -> R` qualifies. Applying a blanket "trust the header" would regress
  the CVs the content-over-hierarchy rule exists to protect (a journal article filed
  under the wrong section really should be S1). The pin should be: *if 3a mapped this
  header to code C with high confidence, and the entry is a table row under it,
  default to C unless content contradicts C with even higher confidence.*

- **Validate across at least two rolls.** Per the post-mortem's own rule — 3b varies
  (278/268/324 entries on identical input). A single post-pin run can manufacture a
  false win.

- **Expect a tension with `R` vs `K` genuinely mixed sections.** A few rows under
  INVITED PRESENTATIONS may truly be courses the author taught by invitation. Pinning
  to R will miscode those the other way. That is likely an acceptable trade — a reader
  scanning a CV expects the invited-presentations section to be populated — but it is
  a real trade, not a clean win, and worth stating in the PR.

## Adjacent bug found in passing (not chased)

In RERUN-2, some publications reached 3b tagged `(Section: BIBLIOGRAPHY >
International)`. `International` is a sub-label of INVITED PRESENTATIONS, not
BIBLIOGRAPHY — the parent-tracking that walks the flat header list attached these
rows to the wrong parent. It did not hurt here (they are journal articles and code S1
on content regardless), but the same misattachment on a non-obvious entry would
misroute it. Worth a separate issue; out of scope for option 7.

## Method / reproduction

```bash
cd src/unified_pipeline/prompt_logs
# section paths 3b actually saw, by run (11:xx = RERUN-1, 13:xx = RERUN-2)
grep -h -o "(Section: [^)]*)" 2026-07-24_13-*stage_3b*READABLE.txt | sort | uniq -c | sort -rn
# then join each [i] entry's section path to the response's classifications[i].code
```

The tally script pairs each `[i] (Section: ...)` line in the USER PROMPT with
`classifications[i]` in the matching `_RESPONSE.json`. All 88 INVITED-path entries,
no sampling.
