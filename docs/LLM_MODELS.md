# LLM Model Strategy

**Status:** Active as of 2026-05-19
**Applies to:** the `unified_pipeline` CV-parsing pipeline and the `web_interface` backend.

## TL;DR

- **Provider:** AWS Bedrock.
- **Model:** Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`) for **every** live pipeline stage.
- Configured in [`src/unified_pipeline/config/llm_config.yaml`](../src/unified_pipeline/config/llm_config.yaml).
- Bedrock bills Claude models at the **same per-token price as Anthropic's direct API** — there is no cost penalty for staying on Bedrock.

## Why Bedrock — and why this is not "Bedrock's models are poor"

Bedrock serves the **same frontier Claude models** as Anthropic's direct API: Opus 4.7, Sonnet 4.6, Haiku 4.5. A model that has performed poorly here was a *Claude 3-generation* model (e.g. `claude-3-haiku`, early 2024) — that is a stale model choice, not a limitation of Bedrock.

Two things make the difference:

1. **Use a current-generation model.** Claude Sonnet 4.6 is a large step up from anything in the Claude 3 family.
2. **Enable Bedrock model access.** Newer Claude models must be explicitly enabled per AWS account/region (see [Bedrock setup](#bedrock-setup) below).

Bedrock vs. the direct Anthropic API is an **operational** choice, not a cost one — per-token prices (and prompt-caching prices) are identical across both. Bedrock uses AWS IAM auth, so when the pipeline runs on AWS infrastructure there is no API key to manage. That is why Bedrock was kept.

## The live pipeline vs. dead code

The repository contains several generations of pipeline code. **Only one pipeline is live** — the 12-stage flow in `run_full_pipeline.py` / `web_interface/.../pipeline/orchestrator.py`. Only the stage IDs below reach an LLM on a normal run; model configuration only matters for these:

| Stage ID | Job | Notes |
|---|---|---|
| `segmentation_chunked_hierarchy` | Extract H1/H2/H3 headers from CV chunks | stage 1a |
| `segmentation_signature` | Normalize / validate the header hierarchy | stage 1a |
| `stage_2` | Group paragraphs/rows into logical entries | once per section |
| `stage_3a` | Map the header outline to WCM taxonomy codes | classification — errors cascade |
| `stage_3b` | Classify entries to taxonomy codes | classification — errors cascade |
| `stage_4` | Extract structured fields from entries | **cost hotspot — 30–60+ calls per CV** |
| `stage_4_5` | Score / write the NIH-style research summary | quality-sensitive long-form generation |
| `stage_5b` | Resolve institution names to city/state/country | terminal enrichment |
| `stage_5c` | Reformat teaching entries | terminal formatting |
| `stage_5d` | Reformat citations to Vancouver style | terminal formatting |
| `stage_6` | Geo-scope classification + multi-activity entry splitting | terminal |

**Dead / non-production stage families** — ignore them for model configuration; they are never reached on a live run: the `core_*` family (legacy `core/cv_pipeline.py`), the `cv_parser_*` and `parser_*` families (older parser generation), the alternative `segmentation_*` strategies (`word_delimited`, `word_original`, `chat_hierarchy`, `entry_extractor`, `pdf_vision`), `stage_2a`, `validator_llm`, and `tool_ab_tester`. They are candidates for deletion in a future cleanup but are out of scope here.

## Model assignment

Every live stage runs **Claude Sonnet 4.6**. There are no per-stage overrides — the whole pipeline inherits the `default` block in `llm_config.yaml`.

**Rationale.** The decision was to err toward the more capable model:

- The classification stages (`stage_3a`, `stage_3b`) and segmentation are *cascade-critical* — a wrong taxonomy code or a botched hierarchy corrupts everything downstream.
- `stage_4` is the hardest extraction work **and** the cost hotspot (a nested loop, 30–60+ calls per CV). Quality here is worth the most.
- `stage_4_5` writes the research summary — the marquee, quality-sensitive output.
- The terminal stages (`stage_5b`, `5c`, `5d`, `stage_6`) are mechanical, but they run at low call volume, so running them on Sonnet rather than a cheaper model costs very little. Keeping a single model also keeps the configuration trivial.

### Cost-saving downgrade path

If cost needs trimming, the terminal, non-cascading stages are the safe place to drop to the cheaper **Claude Haiku 4.5** — they format already-extracted data, so a marginally weaker model there cannot corrupt the pipeline. Uncomment the `stages:` block in `llm_config.yaml`:

```yaml
stages:
  stage_5b: {model: us.anthropic.claude-haiku-4-5-20251001-v1:0}
  stage_5c: {model: us.anthropic.claude-haiku-4-5-20251001-v1:0}
  stage_5d: {model: us.anthropic.claude-haiku-4-5-20251001-v1:0}
  stage_6:  {model: us.anthropic.claude-haiku-4-5-20251001-v1:0}
```

> **Use the full versioned inference-profile ID for Haiku 4.5.** The short alias
> `us.anthropic.claude-haiku-4-5` is rejected by Bedrock (`ValidationException:
> model identifier is invalid`), and stage code swallows the per-batch error and
> silently falls back to default codes — so a misconfigured downgrade looks like
> it works but produces garbage. Confirm the exact ID for your region with
> `aws bedrock list-inference-profiles`. (Sonnet 4.6 happens to expose the short
> `us.anthropic.claude-sonnet-4-6` alias; Haiku 4.5 does not.)

The largest single lever is `stage_4` (the 30–60+ call hotspot). Before changing its model, run an A/B accuracy comparison of Sonnet 4.6 vs. Haiku 4.5 on a sample of real CVs — see [Follow-ups](#follow-ups).

## How model selection works

All LLM calls go through `call_llm(stage="...", ...)` in `src/unified_pipeline/llm_client.py`. The model is resolved by `get_stage_config()` in `src/unified_pipeline/config.py`, with this precedence (later wins):

1. Hardcoded defaults (`openai` / `gpt-4o-mini`).
2. The `default` block in `llm_config.yaml`.
3. A per-stage override under `stages:` in `llm_config.yaml`.
4. The `CVICHE_LLM_PROVIDER` / `CVICHE_LLM_MODEL` environment variables — these override the `default` block **only**, never an explicit per-stage override.

> **Operational caveat — check your deployment environment.** Because env vars override the YAML `default`, a `CVICHE_LLM_MODEL` set in a deployment (Docker, EKS, a `.env` file) silently wins over this repo's `llm_config.yaml`. If a stale `CVICHE_LLM_MODEL` points at a Claude 3-era model, that is what runs regardless of this file. **Verify those env vars** point at `us.anthropic.claude-sonnet-4-6` (or are unset) when rolling this out.

The `model` argument still threaded through some `stage_4` functions is **vestigial** — `call_llm` ignores it; `llm_config.yaml` is the single source of truth. The orchestrator no longer carries a `self.model` field.

## Bedrock setup

The newer Claude models are invoked through **cross-region inference profiles**, not bare model IDs. Sonnet 4.6's profile ID is region-scoped: `us.anthropic.claude-sonnet-4-6` for US regions (`eu.` / `apac.` / `global.` exist for other geographies).

Before the pipeline can call the model:

1. **Enable model access.** In the Bedrock console (Model access page) for the pipeline's AWS account and region, enable access to Claude Sonnet 4.6. This is usually granted instantly for Anthropic models.
2. **Confirm the inference-profile ID** for your region:
   ```
   aws bedrock list-inference-profiles --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'sonnet-4-6')]"
   ```
   If the ID differs from `us.anthropic.claude-sonnet-4-6`, update `llm_config.yaml` and the `PRICING` key in `config.py` to match.
3. **Credentials.** `llm_client.py` calls `boto3.client("bedrock-runtime")`, which can authenticate two ways:
   - **Bedrock API key (bearer token)** — set the `AWS_BEARER_TOKEN_BEDROCK` environment variable to the key. Current boto3 detects it automatically for Bedrock calls; no other AWS credentials are needed. Simplest for local development.
   - **Standard credential chain** — `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars, `~/.aws/credentials`, or an IAM instance role. On AWS infrastructure an IAM role needs no static key.

   Either way, the identity behind the credential still needs Bedrock model access (step 1) and `bedrock:InvokeModel*` permissions — the credential authenticates, it does not grant model access. Outside an interactive shell (a service process or container), the credential must be set in that process's own environment; a developer's `~/.zshrc` is not inherited.

`AWS_DEFAULT_REGION` (default `us-east-1`) selects the Bedrock region.

## Cost

Bedrock and the direct Anthropic API charge the **same per-token price**, including prompt caching. Per-1M-token rates (mirrored in `PRICING` in `config.py`):

| Model | Input | Output |
|---|---|---|
| Claude Sonnet 4.6 (active) | $3.00 | $15.00 |
| Claude Haiku 4.5 (downgrade option) | $1.00 | $5.00 |
| Claude Opus 4.7 | $15.00 | $75.00 |

`calculate_cost()` strips region inference-profile prefixes (`us.` / `eu.` / `apac.` / `global.`) before the `PRICING` lookup, so `us.anthropic.claude-sonnet-4-6` resolves correctly. If a model is missing from `PRICING`, cost is computed at gpt-4o-mini rates and a one-time warning is logged — **add new models to `PRICING` whenever the configured model changes**, or recorded run costs will be wrong.

### The web UI cost estimate

The `/estimate` endpoint's USD-per-1,000-document-token rate is no longer a hardcoded constant. It is derived from the configured model (`estimate_cost_per_1k_doc_tokens()` in `config.py`): an empirically anchored rate is rescaled by the ratio of blended model prices, so the estimate shown on the upload page tracks `llm_config.yaml`. The upload page also displays which model the estimate assumes. `CVICHE_COST_PER_1K_TOKENS`, if set, still pins the rate to an explicit value.

The anchor rate (`COST_ESTIMATE_ANCHOR_RATE` in `config.py`) is calibrated against gpt-4o-era runs and is approximate — see [Follow-ups](#follow-ups).

## Prompt caching

Live as of 2026-05-19. The pipeline re-sends large, stable prompt prefixes (schemas, taxonomy rule-books, instructions) across many calls — especially `stage_4`, where 30–60+ calls per CV share the same system prompt. Bedrock prices cache **reads at 0.1×** the input price and cache **writes at 1.25×** input. For `stage_4`'s prompt-heavy workload this cuts input cost by ~80–90% once the cache is warm; the cache TTL is 5 minutes, which is long enough that the per-CV burst reuses the cached prefix.

### How it works

- `_call_bedrock()` in `llm_client.py` appends a `{"cachePoint": {"type": "default"}}` checkpoint to the Converse `system` block, marking the system prompt as the cache key.
- It then reads `cacheReadInputTokens` / `cacheWriteInputTokens` from the response `usage` and surfaces them in `call_llm()`'s normalized return dict as `cache_read_tokens` / `cache_write_tokens`. With caching on, Bedrock's `inputTokens` reports only the *uncached* portion, so `call_llm` re-synthesizes `prompt_tokens = uncached + cache_read + cache_write` to keep downstream token accounting intact.
- `calculate_cost()` in `config.py` accepts `cache_read_tokens` / `cache_write_tokens` and prices them at 0.1× / 1.25× the model's input rate. Cache pricing matches Anthropic's direct API.
- The pipeline orchestrator's `update_cost()` accumulates the cache split per run, and the `Run` table carries `cache_read_tokens` / `cache_write_tokens` columns (Alembic revision `d7a4f9b2e103`). The WebSocket `COST_UPDATE` event also includes the deltas.

### Config flag

Caching is on by default. Toggle it in `llm_config.yaml`:

```yaml
default:
  enable_prompt_caching: true   # default; set false to disable
```

When `enable_prompt_caching: false`, the Converse request body is **byte-identical** to the pre-caching shape — useful for isolating caching-related issues or testing against models that don't support it.

### Operational notes

- Only the `system` block is cached. The schema/instruction prefix lives there; per-call entry data goes in the user message and is intentionally outside the cache key.
- The cached prefix must be ≥ Claude's minimum cacheable size (~1024 tokens for Sonnet 4.6). Short system prompts won't trigger a cache write — and that's fine, since the savings would have been negligible anyway.
- Cache token counts from non-`stage_4` stages are not yet aggregated into `Run.cache_read_tokens` / `cache_write_tokens` — their `cost` field is still accurate via `calculate_cost`, but the split is reported as 0. Wiring the remaining stages up is a small follow-up; the hot path (`stage_4`) is wired today and accounts for the vast majority of LLM calls.

## Follow-ups

- **Eval `stage_4` on Haiku 4.5.** It is the dominant cost. A/B its field-extraction accuracy against Sonnet 4.6 on real CVs; if Haiku holds up, downgrading saves ~5× on the biggest line item.
- **Aggregate cache tokens for the remaining stages.** Today only `stage_4` surfaces a cache-token split into `Run.cache_read_tokens` / `cache_write_tokens`. The cost is still accurate everywhere via `calculate_cost`, but other stages report 0 cache tokens; wiring them up is a mechanical follow-up.
- **Recalibrate the UI estimate.** `COST_ESTIMATE_ANCHOR_RATE` is approximate. Once enough runs accumulate with correct `PRICING`, recompute it from real `run.total_cost` data (this needs the document-token count persisted per run). With prompt caching live, recalibration should be done against post-caching cost data so the rate reflects warm-cache reality.
- **Delete dead code.** The `core_*`, `cv_parser_*`, `parser_*`, and alternative `segmentation_*` modules are unreachable on the live path.
- **Stale model references remain in comments** — e.g. the module docstring of `extraction_failure_detector.py` describes a gpt-4o-mini/gpt-5.1 tiering that no longer reflects reality.

## Change log

### 2026-05-19 — Bedrock prompt caching

- `src/unified_pipeline/config/llm_config.yaml` — added `enable_prompt_caching: true` to the `default` block.
- `src/unified_pipeline/config.py` — `calculate_cost()` now accepts `cache_read_tokens` / `cache_write_tokens` and prices them at 0.1× / 1.25× the model's input rate.
- `src/unified_pipeline/llm_client.py` — `_call_bedrock()` appends a `cachePoint` to the system block when caching is enabled; `call_llm()` surfaces `cache_read_tokens` / `cache_write_tokens` in its return dict.
- `src/unified_pipeline/stage_4_field_extractor.py` — aggregates cache tokens through the batch loop and emits them in the stage output.
- `web_interface/backend/app/models.py` — added `cache_read_tokens` / `cache_write_tokens` columns to the `runs` table.
- `web_interface/backend/alembic/versions/d7a4f9b2e103_add_cache_token_columns_to_runs.py` — migration for those columns.
- `web_interface/backend/app/pipeline/orchestrator.py` — `update_cost()` accepts cache deltas and writes them to `Run`.
- `web_interface/backend/app/pipeline/event_emitter.py` — `COST_UPDATE` events now carry the cache deltas and totals.

### 2026-05-19 — Bedrock Claude Sonnet 4.6

- `src/unified_pipeline/config/llm_config.yaml` — provider → `bedrock`, model → Claude Sonnet 4.6; removed stale `gpt-4o` overrides.
- `src/unified_pipeline/config.py` — added Bedrock Claude 4.x pricing; `calculate_cost()` region-prefix normalization + warn-once on missing pricing; cost-estimate helpers (`estimate_cost_per_1k_doc_tokens`, `friendly_model_name`).
- `src/unified_pipeline/stage_4_field_extractor.py` — neutralized the dead `model="gpt-5.1"` parameter and stale docstrings.
- `web_interface/backend/app/pipeline/orchestrator.py` — removed the unused `self.model` field.
- `web_interface/backend/app/services/config_service.py` — cost rate now derived from the configured model.
- `web_interface/backend/app/api/upload.py` — `/estimate` uses the derived rate and returns `pricing_model`.
- `web_interface/frontend/src/types/upload.ts`, `components/UploadPage.tsx` — display the model the estimate is based on.
- `web_interface/backend/tests/test_service_layer.py` — updated the cost-estimation test.
