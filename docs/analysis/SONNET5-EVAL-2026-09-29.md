# Sonnet 5 evaluation (2026-09-29)

Decision record: `docs/adr/0001-sonnet-5-default-model.md`.

**Outcome:**
- #1077 (Sonnet 5 support, cap removal, null-location fix) merged 2026-09-29.
- #1109 (default switch, tokenizer-aware `/estimate`) merged 2026-09-30.
- IAM `CvicheBedrockPolicy` v15 adds the Sonnet 5 `us.` profile.
- Deployed as `dev-221` (`43d692a4`) on 2026-09-30, verified in-pod with a live Sonnet 5 call through the pod role.

## Decision rule (stated before results)

- **Adopt** if quality is not worse and real Sonnet-stage cost per CV is at least 15% lower.
- **Reject** if a quality measure regresses in a way attributable to the model rather than run-to-run noise.
- **Inconclusive** otherwise.

## Verdict

**Adopt, after #1077 is merged and deployed.** On the default code, Sonnet 5 *regressed*, and it would be a reject. Two defects caused that, and both are fixed in #1077 and apply to Sonnet 4.6 as well:

1. Tight call-site `max_tokens` caps. The new tokenizer spends about 1.2–1.4x the tokens on the same text, so the stage 4.5 summary (cap 350) was cut mid-sentence on 9 of 13 calls, and owner-location inference (cap 500) truncated. Sonnet 4.6 also truncated one summary, so production is already affected.
2. Location inference rejected a `null` city or state. Sonnet 5 writes `null` where Sonnet 4.6 writes `""`. The rejection failed the owner-location gate (minus 10.5 points) and, through stage 6's geographic-scope fallback, filed every invited talk under National.

With those fixed, quality is equivalent or slightly better, and Sonnet-stage cost is **15.8% lower** on 10 CVs (16.9% on the uncapped 5-CV re-run). That clears the 15% bar, but narrowly.

**Recommendation (adopted):** land #1077, then switch `default.model` to `us.anthropic.claude-sonnet-5`. Expect about $140 a month saved on September's roughly $897 of Sonnet spend, with runs about 18% slower.

## Setup

- **Code:** both arms ran from the same commit, `489fad42` on #1077's branch, in separate worktrees. Per-stage tokens and cost come from each run's `prompt_logs/<sha256(uid)[:10]>/*_RESPONSE.json`. The raw per-CV artifacts were deleted afterwards (they contain CV-derived PII), so the tables below are the surviving record.
- **Arms:**
  - A: default (Sonnet 4.6, with 3b on Haiku 4.5).
  - B: `CVICHE_LLM_MODEL=us.anthropic.claude-sonnet-5`. The `Models:` line confirms 3b stayed on Haiku in both arms.
  - B3: Sonnet 5 on `f29a2f07`, with the call-site caps removed, run on 5 CVs.
  - B2: 1.5x cap scaling, aborted and superseded by B3.
- **Probe:** Sonnet 5 400s on `temperature` ("deprecated for this model"). Forced tool choice works with thinking omitted or disabled. #1077 omits temperature and sends `thinking: disabled`.
- **Sonnet 5.5** still has only a `global.` profile in us-east-1, us-east-2 and us-west-2 (re-checked 2026-09-29).
- **Health:** 0 outage or throttling hits, 0 stage 3b batch failures, 0 400s, and 10 of 10 docx rendered in each arm.
- **Stage 3b cost is excluded.** The arms ran concurrently with identical Haiku prompts, so arm B read arm A's prompt cache. 3b is Haiku in both arms and out of scope.

## CVs (batch-4 set, stratified by size)

| CV | Chars | CV | Chars |
|---|---|---|---|
| web35 | 5.2K | web175 | 36.4K |
| web162 | 6.7K | web39 | 41.5K |
| web22 | 15.4K | web196 | 58.2K |
| web206 | 22.9K | web202 | 105.3K |
| web222 | 28.5K | web210 | 130.1K |

## Per CV

Sonnet stages only. Tokens are total input (uncached, cache read and cache write) and output.

| CV | Cost A | Cost B | B/A | In A | In B | Out A | Out B | Wall A (min) | Wall B (min) | Score A | Score B | Score B3 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| web162 | $0.40 | $0.36 | 0.90 | 51K | 79K | 14K | 18K | 2.2 | 3.3 | 89 | 87 | 87 |
| web175 | $1.59 | $1.28 | 0.80 | 136K | 181K | 71K | 83K | 6.7 | 6.9 | 92 | 82 | 82 * |
| web196 | $2.43 | $2.03 | 0.83 | 176K | 244K | 113K | 137K | 8.8 | 10.7 | 96 | 85 | 96 |
| web202 | $2.83 | $2.35 | 0.83 | 214K | 301K | 130K | 154K | 10.0 | 13.6 | 93 | 93 | |
| web206 | $0.66 | $0.51 | 0.78 | 77K | 104K | 25K | 27K | 2.9 | 3.0 | 70 | 86 | 78 |
| web210 | $4.12 | $3.54 | 0.86 | 397K | 587K | 171K | 206K | 21.5 | 24.8 | 90 | 90 | |
| web22 | $0.81 | $0.67 | 0.82 | 82K | 113K | 33K | 40K | 3.8 | 4.1 | 88 | 89 | |
| web222 | $0.97 | $0.90 | 0.93 | 99K | 150K | 40K | 54K | 4.2 | 5.1 | 95 | 94 | |
| web35 | $0.35 | $0.30 | 0.85 | 46K | 68K | 14K | 16K | 2.2 | 2.5 | 87 | 76 | 87 |
| web39 | $1.36 | $1.15 | 0.84 | 141K | 205K | 56K | 66K | 5.3 | 6.3 | 93 | 93 | |
| **Total** | **$15.53** | **$13.07** | **0.842** | **1.42M** | **2.03M** | **666K** | **802K** | **68** | **80** | | | |

\* web175's B3 score still carries the `null` location failure. It is fixed by #1077's last commit (`d11c197f`): replaying all 28 logged location responses, the old model rejects 5 and the new one rejects 0. This was not re-run live.

- **Score drops are location failures.** Every B-arm drop of more than 2 points is the owner-location gate, minus 10.5, on web175, web196 and web35. Truncation caused two of them, which B3 fixed. The `null` rejection caused web175's.
- **web206 is noise.** It scored 70 and 74 on Sonnet 4.6 (this run and batch 4), and 86 and 78 on two Sonnet 5 runs of the same code, so its range covers both models.

## Tokens and cost

- **Tokenizer inflation, measured:** 1.43x on input and 1.20x on output. That's above the handoff's 1.0–1.35x estimate, and it eats about half of the 33% per-token discount.
- **By stage** (A to B cost): stage 2 $3.98 to $3.19, stage 4 $6.68 to $5.60, stage 5d $2.50 to $2.12. The flat stages (segmentation, 5c, 6) are small in absolute terms.
- **Removing the caps costs almost nothing:** Sonnet 5 went from 0.824x to 0.831x of Sonnet 4.6 on the 5-CV subset.
- **Projection:** 15.8% of about $897 is about $142 a month, or about $1,700 a year. On the 5-CV uncapped subset it's 16.9%.
- **The Cost Explorer cross-check agrees.** For 2026-09-29, Sonnet 5 was billed $19.82 (grouped by USAGE_TYPE), against $19.77 recorded across the three Sonnet 5 arms, a 0.3% gap. The billed unit rates are exactly $2.20 input, $11.00 output, $2.75 cache write and $0.22 cache read per 1M tokens, which verifies the `PRICING` entry.

## Quality

- **Truncation** (`finish_reason=length`, all arms):
  - Sonnet 4.6: 1, a stage 4.5 summary.
  - Sonnet 5 capped: 14 (9 stage 4.5, 5 stage 4).
  - Sonnet 5 uncapped (B3): 0.
- **Doctor gate** (`doctor_gate.py`, same code, A vs B):
  - Arm B has 1 new WARN (`stage6_render_warnings` on web202).
  - 16 WARNs are gone in B: `missed_headers` 11, `stage6_render_warnings` 3, `under_extraction` 1, `classified_unrendered` 1.
  - On web196, 5 of the vanished `missed_headers` WARNs came back as INFO.
- **Render gate** (same code, same day): all 10 CVs changed, as expected between two LLM runs.
  - The normalized paragraph churn is 5–15% in each direction and symmetric.
  - Arm B renders the same or more text on every CV. web206 is +40%, because arm A had dropped a professorship and its courses to the Appendix.
- **Entry counts** (`summary.tsv`): within ±3% on 9 of 10 CVs. On web206, B has 170 against A's 211, but that's run-to-run noise: batch 4 got 176 on Sonnet 4.6.
- **Spot-reads** (blind agents; counts only, checked against the source):

| CV | Compared | Verdict |
|---|---|---|
| web210 (largest) | A vs B | Equivalent, B marginally ahead: 29 of 29 grants identical, the same 86 core publications, fewer misplaced positions and abstracts, and 1 fewer missing date |
| web175 | A vs B3 | Equivalent: grants, positions and publications are complete in both. B dates the licenses correctly. B's all-National talk tables are the `null` location defect (fixed). |
| web206 | A vs B3 | A worse: it loses a professorship and its 6 courses and 2 of 3 grants. B misplaces duty bullets and invents one "Present" date. |

- **Research summaries:** none of the three spot-read CVs has a research summary in the source.
- **Correction (2026-09-30), publication counts.** An earlier version of this doc said both arms drop the same 13 of about 18 web206 articles. That was wrong.
  - All 20 publication records render.
  - `_fill_bibliography` writes enriched citations as tracked insertions (`w:ins`), and the spot-read agents counted with python-docx `paragraph.text`, which skips those. 14 web206 bibliography paragraphs carry their only text inside `w:ins`.
  - The publication counts in the spot-reads undercount in absolute terms, for both arms equally. Both arms render through the same stage-6 code, so the A-vs-B verdicts are unaffected.
- **Found in passing, not model-related:** web206's published dissertations are classified S7 and render under the template's "In review" header in both arms. S7 means "gray literature" to stage 3b but "In review" to stage 6. Filed as #1166.

## Caveats

- 10 CVs is a small sample, and the 15.8% saving is only 0.8 points over the bar.
- There's no Sonnet 4.6 noise-floor re-run, because the budget was spent. web206's cross-batch spread (70–86) stands in for one.
- The two fixes are proven by replay and by the code path, not by a live Sonnet 5 re-run of web175 after commit 4.
- Total spend was $40.04 (A $17.59, B $14.83, B2 $2.41, B3 $5.21), including Haiku 3b. That's at the $40 line.
