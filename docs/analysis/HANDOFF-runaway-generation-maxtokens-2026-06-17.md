# Handoff — bound runaway generation with an explicit `maxTokens` floor

**Status:** ready to implement. Small, contained change in the LLM client + config.
**Raised by:** Mahender, 2026-06-17 (the "default is 64,000 tokens" observation).
**Type:** safety / cost-risk hardening — **not** a cost reduction (see "What this is and isn't").

## What this is and isn't

This bounds the **worst case**, it does **not** lower the normal bill.

- On Bedrock you are billed for output tokens **actually generated**, never for the `maxTokens` ceiling. A cap is an upper bound, not a reservation.
- So setting caps will **not** reduce spend on well-behaved calls. What it does: if a prompt ever misbehaves (repetition, the model not stopping, a bad input), an **uncapped** call can run all the way to the model's default ceiling (~64K output tokens) and you are billed for every token it actually emits. The cap turns that unbounded tail risk into a bounded one.
- The real cost levers (input tokens × hundreds of calls per CV, and prompt-cache hit rate) are a **separate** workstream — do not conflate them with this.

## The mechanism (why some calls are uncapped today)

CViche calls Bedrock through the **Converse API** (`client.converse(...)`), via `_call_bedrock` in `src/unified_pipeline/llm_client.py`.

The brake is one conditional:

```python
# src/unified_pipeline/llm_client.py:412-413
if max_tokens is not None:
    call_kwargs["inferenceConfig"]["maxTokens"] = max_tokens
```

When `max_tokens is None`, `maxTokens` is **omitted entirely** from the request, so Bedrock applies the **model's own default ceiling** — for the models in use (Sonnet 4.6 default, Haiku 4.5 for stage 3b) that is the **64,000** Mahender observed.

How a call ends up with `max_tokens=None`:

- `call_llm` resolves it as `max_tokens = kwargs.get("max_tokens", config["max_tokens"])` — `src/unified_pipeline/llm_client.py:489`.
- The layer-1 default is `"max_tokens": None` — `src/unified_pipeline/config.py` (`get_stage_config`, ~lines 498–503).
- The shipping `src/unified_pipeline/config/llm_config.yaml` sets **no** `max_tokens` (default block or per-stage; only `stage_3b` overrides the *model*).
- Net: **any LLM call that does not pass an explicit `max_tokens=` runs uncapped at ~64K.**

## What's already safe vs. at risk

**Already bounded** — these call sites pass an explicit cap (sample, from grep):

| Stage / call site | `max_tokens` |
|---|---|
| `stage_4_field_extractor.py:1956` | 500 |
| `stage_4_5_research_summary.py:340 / :399` | 200 / 350 |
| `stage_3b_entry_classifier.py:1173` | 2000 |
| `stage_3a_header_taxonomy_mapper.py:315` | 8000 |
| `stage_5c_teaching_formatter.py:278` / `stage_5d_citation_formatter.py:218` | 8000 |
| `stage_6_word_template.py:7304` | 1000 |
| `core/taxonomy_mapper*.py`, `core/candidate_surfacer.py`, `core/extraction_recovery.py` | 500–2500 |
| `segmentation/word_*.py`, `pdf_vision.py` | 4000–16000 |
| `validators/llm_validator.py:147` | 4000 |

**At risk** — any `call_llm(stage, messages, …)` invocation that omits `max_tokens` (and whose stage has no YAML `max_tokens`). Those fall through to `None` → uncapped. **Step 1 of the task is to enumerate these.**

## The task

1. **Audit.** Enumerate every LLM invocation and flag the ones that pass no `max_tokens` (i.e. run uncapped). `grep -rn "call_llm\|_call_bedrock" src` and check each for an explicit `max_tokens=`.
2. **Add a global floor in the client (recommended — belt-and-suspenders).** In `_call_bedrock`, when `max_tokens is None`, substitute a conservative default instead of omitting it, so **no** path can ever be truly uncapped regardless of call-site discipline:

   ```python
   # src/unified_pipeline/llm_client.py — replace the omit-when-None branch
   DEFAULT_MAX_TOKENS = 16000  # module-level; conservative ceiling, ~ the largest legitimate stage output, well under the 64K model max
   call_kwargs["inferenceConfig"]["maxTokens"] = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
   ```

3. **Set explicit per-stage values in `llm_config.yaml`** for any stage whose legitimate output is well below the global floor (most are — extraction/classification stages need only hundreds to a couple thousand). This makes the cap visible and tunable, and tightens the bound further than the global floor.
4. **Verify no truncation.** Each cap must sit **above** the stage's largest legitimate output. After capping, confirm `finish_reason` is not flipping to `max_tokens` on real CVs (the client records `finish_reason` per call; check `prompt_logs`). A cap that truncates real output is worse than the cost risk it prevents.

## Acceptance criteria

- No LLM call path can reach Bedrock without an explicit or floored `maxTokens` (verify: temporarily assert in `_call_bedrock` that `maxTokens` is always present).
- Every stage's cap is ≥ its observed maximum legitimate output — **no new `finish_reason: max_tokens` truncations** on a representative CV set.
- A deliberately-runaway prompt is bounded at the configured cap, not ~64K.

## Pointers

- `src/unified_pipeline/llm_client.py:412-413` — the omit-when-`None` branch (the brake location).
- `src/unified_pipeline/llm_client.py:489` — `max_tokens` resolution from kwargs/config.
- `src/unified_pipeline/config.py` ~498–503 — layer-1 default `max_tokens: None`.
- `src/unified_pipeline/config/llm_config.yaml` — runtime config (no `max_tokens` set today).

## Out of scope (do not bundle)

- Input-token / prompt-cache cost work (the "are we paying for redundant/unused input tokens" thread). Different problem, different fix.
- Lowering caps to "save money" — restate to stakeholders: caps bound worst-case risk; they don't reduce spend on normal calls.
