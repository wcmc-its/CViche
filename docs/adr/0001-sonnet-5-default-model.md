# ADR 0001: Claude Sonnet 5 as the default pipeline model

- **Status:** Accepted, 2026-09-29. Takes effect with the follow-up change to `default.model` in `llm_config.yaml`, which lands after #1077.
- **Deciders:** Paul Albert

## Context

Every pipeline stage except 3b runs on the YAML `default` model, Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`). Stage 3b runs on Haiku 4.5. In September, Sonnet 4.6 accounted for about $897 of roughly $1,045 in Bedrock spend.

Claude Sonnet 5 is the newer model in the same tier. At Bedrock's `us.` regional rates it costs $2.20 / $11.00 per 1M input / output tokens, against $3.30 / $16.50 for Sonnet 4.6. Its tokenizer yields more tokens for the same text, so the real saving is smaller than the 33% list difference.

Two constraints narrowed the choice:

- **US-only processing.** Faculty CVs must stay in the US, so only `us.` inference profiles are allowed, never `global.`.
- **No Sonnet 5.5.** It costs the same, but as of 2026-09-29 it has no `us.` profile in us-east-1, us-east-2 or us-west-2, and it rejects the forced tool choice that `json_schema` calls depend on.

## Decision

Make Sonnet 5 (`us.anthropic.claude-sonnet-5`) the default model. Stage 3b stays on Haiku 4.5; that choice rests on its own gold evaluation (2026-05-26).

## Basis

The decision rule was set before the evaluation: adopt only if quality is not worse and the real Sonnet-stage cost per CV is at least 15% lower.

**Cost.** A 10-CV A/B on 2026-09-29 ran both arms from the same commit. The Sonnet stages cost 15.8% less on Sonnet 5 ($13.07 against $15.53), and 16.9% less on a 5-CV re-run with the call-site caps removed. The tokenizer produced 1.43x the input tokens and 1.20x the output tokens. At September's volume, the projected saving is about $140 a month.

**Quality: no regression once #1077 is in.** Before it, Sonnet 5 regressed on 3 of 10 CVs, for two reasons that #1077 fixes:

- **Tight call-site `max_tokens` caps** truncated its output. They were sized for Sonnet 4.6's tokenizer, and Sonnet 4.6 also hit one of them.
- **Location inference rejected a `null` city or state**, which Sonnet 5 writes where Sonnet 4.6 writes `""`.

With those fixes, the evidence is:

- **Quality scores:** within 2 points of Sonnet 4.6 on 7 of 10 CVs.
- **Entry counts:** within ±3% on 9 of 10 CVs.
- **Doctor findings:** 1 new warning and 16 cleared.
- **Blind spot-reads** of 3 CVs against their source documents: Sonnet 5 was equal or better on each, with fewer missing dates and misplaced records, though it made errors of its own.

**Accuracy: assumed, not measured.** Sonnet 5 is the newer and more capable model in this tier, and the spot-reads lean its way. But this evaluation did not show it is more accurate:

- 10 CVs is small.
- There was no Sonnet 4.6 re-run to measure run-to-run noise, and one CV's score ranged from 70 to 86 across four runs of the two models.
- The quality score is a structural heuristic, not ground truth.

We adopt on cost plus non-regression, and **assume** better accuracy rather than claiming it.

The full evaluation is in `docs/analysis/SONNET5-EVAL-2026-09-29.md`, a local analysis file that is not committed.

## Consequences

- **Cost:** about 16% less Sonnet spend per CV at current volume.
- **Speed:** runs are about 18% slower (80 against 68 minutes on the 10-CV set).
- **Token caps:** any `max_tokens` cap sized from Sonnet 4.6's output lengths is about 1.2–1.4x too tight for Sonnet 5. #1077 removes the tight call-site caps on the Sonnet stages, leaving `DEFAULT_MAX_TOKENS` (16K) to bound runaway output. **Do not add a tight call-site cap back**; if a length limit is wanted, put it in the prompt.
- **Model-specific requests:** Sonnet 5 rejects `temperature` and thinks unless told not to. `_call_bedrock` handles both for models in `NO_SAMPLING_PARAMS_MODELS`. Any future model with the same restrictions needs adding to that set.
- **Pricing:** `PRICING` carries a Sonnet 5 entry. Without it, recorded costs would silently fall back to Sonnet 4.6's rates.
- **Rollback** is the same one-line change to `default.model`, reversed. Both models stay supported.

## Revisit when

- A `us.` profile for Sonnet 5.5 or a later Sonnet appears in our regions. Before adopting it, check that it accepts forced tool choice.
- The data-residency position on `global.` profiles changes.
- A gold-set field-accuracy measurement becomes available. That would turn the accuracy assumption above into a measurement.
- Production quality or doctor trends move against Sonnet 5 in the first weeks after the switch.
