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
  stage_5b: {model: us.anthropic.claude-haiku-4-5}
  stage_5c: {model: us.anthropic.claude-haiku-4-5}
  stage_5d: {model: us.anthropic.claude-haiku-4-5}
  stage_6:  {model: us.anthropic.claude-haiku-4-5}
```

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
3. **Credentials.** `llm_client.py` uses the boto3 default credential chain (env vars → `~/.aws/credentials` → IAM instance role). On AWS infrastructure an IAM role is sufficient — no API key.

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

## Prompt caching (recommended, not yet implemented)

The pipeline re-sends large, stable prompt prefixes (schemas, taxonomy rule-books, instructions) across many calls — especially `stage_4`, where 30–60+ calls per CV share the same schema block. Bedrock supports prompt caching for Claude with cache **reads at 0.1×** the input price; for a prompt-heavy pipeline this can cut input cost by ~80–90%.

Implementation sketch for a future change:

- In `_call_bedrock()` (`llm_client.py`), append a cache checkpoint to the Converse `system` block: `{"cachePoint": {"type": "default"}}` after the system text.
- Read the cache token counts from the Converse response `usage` and price cache reads at 0.1× / cache writes at 1.25× input in `calculate_cost()` so recorded costs stay accurate.
- Bedrock's cache TTL is 5 minutes — long enough that `stage_4`'s burst of calls for one CV reuses the cached prefix.
- Guard it behind a config flag; caching requires a supporting model (Sonnet 4.6 qualifies).

## Follow-ups

- **Eval `stage_4` on Haiku 4.5.** It is the dominant cost. A/B its field-extraction accuracy against Sonnet 4.6 on real CVs; if Haiku holds up, downgrading saves ~5× on the biggest line item.
- **Implement prompt caching** (above) — the highest-value cost optimization.
- **Recalibrate the UI estimate.** `COST_ESTIMATE_ANCHOR_RATE` is approximate. Once enough runs accumulate with correct `PRICING`, recompute it from real `run.total_cost` data (this needs the document-token count persisted per run).
- **Delete dead code.** The `core_*`, `cv_parser_*`, `parser_*`, and alternative `segmentation_*` modules are unreachable on the live path.
- **Stale model references remain in comments** — e.g. the module docstring of `extraction_failure_detector.py` describes a gpt-4o-mini/gpt-5.1 tiering that no longer reflects reality.

## Change log

This strategy was introduced on 2026-05-19. Files changed:

- `src/unified_pipeline/config/llm_config.yaml` — provider → `bedrock`, model → Claude Sonnet 4.6; removed stale `gpt-4o` overrides.
- `src/unified_pipeline/config.py` — added Bedrock Claude 4.x pricing; `calculate_cost()` region-prefix normalization + warn-once on missing pricing; cost-estimate helpers (`estimate_cost_per_1k_doc_tokens`, `friendly_model_name`).
- `src/unified_pipeline/stage_4_field_extractor.py` — neutralized the dead `model="gpt-5.1"` parameter and stale docstrings.
- `web_interface/backend/app/pipeline/orchestrator.py` — removed the unused `self.model` field.
- `web_interface/backend/app/services/config_service.py` — cost rate now derived from the configured model.
- `web_interface/backend/app/api/upload.py` — `/estimate` uses the derived rate and returns `pricing_model`.
- `web_interface/frontend/src/types/upload.ts`, `components/UploadPage.tsx` — display the model the estimate is based on.
- `web_interface/backend/tests/test_service_layer.py` — updated the cost-estimation test.
