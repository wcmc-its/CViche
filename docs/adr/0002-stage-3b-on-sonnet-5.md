# ADR 0002: Stage 3b on Claude Sonnet 5

- **Status:** Accepted, 2026-10-01. Supersedes the "Stage 3b stays on Haiku 4.5" clause of ADR 0001.
- **Deciders:** Paul Albert

## Context

Stage 3b assigns every extracted entry its taxonomy code, which decides the CV section it renders in. It has run on Haiku 4.5 since May 2026, when a frontier-adjudicated gold set of 147 ambiguous entries scored Haiku 4.5 above Sonnet 4.6 (66.0% against 57.8%). Every other stage moved to Sonnet 5 under ADR 0001; 3b was never re-measured against it.

## Decision

Stage 3b runs on the default model, Sonnet 5 (`us.anthropic.claude-sonnet-5`). The Haiku override is removed from `llm_config.yaml`; the comment there says how to restore it.

## Basis

Both measurements ran 3b alone, from saved stage-2 and stage-3a inputs, on current `dev`. Neither arm had a failed batch.

**The May gold set, re-run (147 entries, 10 CVs).** Haiku 4.5 scored 97 (66.0%, the same as in May) and Sonnet 5 scored 87 (59.2%). The split matters more than the total:

| Gold entries | Haiku 4.5 | Sonnet 5 |
|---|--:|--:|
| Header or junk rows, gold `T` (48) | 46 | 28 |
| Real content entries (99) | 51 | 59 |

**A blind adjudication on new CVs (192 entries, 6 CVs).** On six web-harvested CVs outside the gold set, the two models disagreed on 192 of 1,555 entries (12%). GPT-5.6 Sol (high effort) judged each disagreement without knowing which model gave which answer. It sided with Sonnet 5 on 135, Haiku on 42, and neither on 15.

Two adjustments, both of which keep Sonnet 5 well ahead:

- 44 items were one block of conferences the CV owner attended, which Haiku coded as CME delivered (K4) instead of training received (B2). Without them: Sonnet 5 91, Haiku 42, neither 15.
- Two of the CVs look like versions of a third, so their items repeat. Without them and the attendance block: Sonnet 5 63, Haiku 34, neither 11.

Haiku's losses are systematic: attended training as K4, one-off grant reviews as study-section service (Q3 for Q2, 16 items), and "in press" papers kept as in review (S7 for S1, 12 items). The last one is the gap that prompt text failed to close in #1173.

**Cost and time.** On the six CVs, 3b cost $5.10 on Sonnet 5 against $1.98 on Haiku (2.6×), about $0.52 more per CV. It took 178 s against 135 s across the six. Paul accepted the cost.

## Consequences

- **Header and junk rows get worse.** Sonnet 5 coded 18 of the 48 gold `T` rows as content (A, R, D1). These are usually stray headings and fragments that reach 3b from upstream segmentation. Watch the doctor's Appendix and duplication lints after deploy.
- **The cost estimate gets more accurate.** `estimate_run_cost_usd` already priced 3b tokens at the default model's rate, so it overstated 3b on Haiku.
- **Both evaluations have limits.** Each gold label comes from one model adjudicator, and the six CVs include near-duplicates. A sample labelled by a person would settle the `T`-row trade-off.
