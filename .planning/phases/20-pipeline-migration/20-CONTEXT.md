# Phase 20: Pipeline Migration - Context

**Gathered:** 2026-03-31
**Status:** Ready for planning

<domain>
## Phase Boundary

Migrate all 46 pipeline files from direct OpenAI SDK calls to the centralized `call_llm()` built in Phase 19. After this phase, zero pipeline files import `openai` directly -- all LLM calls go through `llm_client.py`. Three files are documented exceptions (Assistants API, async). Cleanup of redundant code (retry loops, local clients, hardcoded models, per-file cost calculations) is in scope.

</domain>

<decisions>
## Implementation Decisions

### Migration batching & ordering
- **D-01:** Migrate by directory group, one plan per group: parsers (10 files) -> segmentation (12 files, minus 2 exceptions) -> core (9 files, minus 1 exception) -> stages + cv_parser + validators (16 files). ~4 plans.
- **D-02:** Parsers first -- they all follow the exact same `client.chat.completions.create()` pattern, building confidence before tackling more complex files.
- **D-03:** One atomic commit per plan (per directory batch). Not per-file.
- **D-04:** Clean swap with no migration comments. Git history documents the change.

### Non-chat-completions exceptions
- **D-05:** `segmentation/assistants_hierarchy_extractor.py` and `segmentation/direct_file_hierarchy_extractor.py` are documented exceptions -- they use the OpenAI Assistants API (threads, file uploads, polling) which is fundamentally different from chat completions. Leave as-is.
- **D-06:** `core/async_rate_limiter.py` is a documented exception -- it uses `AsyncOpenAI` for concurrency. `call_llm()` is synchronous. Leave as-is.
- **D-07:** The "zero direct OpenAI imports" success criterion applies to all pipeline files EXCEPT these 3 documented exceptions and `llm_client.py` itself.

### Verification
- **D-08:** Run existing test suite after each plan. After the final plan, grep confirms zero direct OpenAI imports in pipeline files (excluding the 3 exceptions).
- **D-09:** Final plan includes a migration audit step: grep-based confirmation of zero remaining imports, with the 3 exception files explicitly documented.
- **D-10:** End-to-end sample CV pipeline run is a Phase 21 concern (TEST-02), not Phase 20.

### Cleanup scope
- **D-11:** Remove redundant code alongside each migration: local `client = OpenAI()` initialization, hand-rolled retry loops (call_llm handles retries per D-13 from Phase 19), and related dead code. Keep changes focused on LLM-related code only.
- **D-12:** Remove all hardcoded model strings (e.g., `'gpt-4o-mini'`, `'gpt-4o'`). Let `call_llm()` use config-driven model selection per stage (D-04, D-06 from Phase 19).
- **D-13:** Remove per-file cost calculation code. `call_llm()` returns cost in the normalized response dict (D-11 from Phase 19). Use `result['cost']` instead.

### Claude's Discretion
- Exact migration mechanics per file (how to adapt each file's specific call pattern to `call_llm()`)
- Whether to refactor function signatures that were structured around the old call pattern
- How to handle files with multiple LLM calls (multiple `call_llm()` invocations vs. restructuring)
- `prompt_ab_tester.py` and `prompt_logger.py` migration approach (tooling files, not pipeline stages)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 19 foundation (what call_llm provides)
- `.planning/phases/19-abstraction-foundation/19-CONTEXT.md` -- D-10 through D-13: call_llm() interface, normalized response, retry behavior
- `src/unified_pipeline/llm_client.py` -- The centralized client module (call_llm signature, response shape, error handling)
- `src/unified_pipeline/config.py` -- get_stage_config() for per-stage model resolution
- `src/unified_pipeline/config/llm_config.yaml` -- YAML config with default + per-stage overrides

### Requirements
- `.planning/REQUIREMENTS.md` -- LLM-03: "All 46 pipeline files migrated to centralized LLM client"
- `.planning/ROADMAP.md` -- Phase 20 scope and success criteria

### Migration targets (files to migrate)
- `src/unified_pipeline/parsers/` -- 10 parser files (all follow same pattern)
- `src/unified_pipeline/segmentation/` -- 12 files (minus 2 Assistants API exceptions)
- `src/unified_pipeline/core/` -- 9 files (minus 1 async exception)
- `src/unified_pipeline/cv_parser/` -- 4 files
- `src/unified_pipeline/validators/` -- 1 file
- `src/unified_pipeline/stage_*.py` -- 11 top-level stage files

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `llm_client.py:call_llm()` -- The target function all files will migrate to. Accepts `stage`, `messages`, `response_format`, `**kwargs`.
- `config.py:get_stage_config()` -- Resolves per-stage model/provider/temperature from YAML config with env var overrides.
- Normalized response dict: `content`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost`, `model`, `provider`, `finish_reason`, `latency_ms`.

### Established Patterns (current, to be replaced)
- Most files: `client = OpenAI()` at module level, then `response = client.chat.completions.create(model=..., messages=..., response_format=...)`.
- Some files: function-local `client = OpenAI()` instantiation.
- Several files: hand-rolled retry loops with `time.sleep()` backoff.
- Several files: inline `calculate_cost()` calls after getting responses.
- Response access: `response.choices[0].message.content` -> will become `result["content"]`.
- Token access: `response.usage.prompt_tokens` -> will become `result["prompt_tokens"]`.

### Integration Points
- Every `call_llm()` invocation needs a `stage` parameter matching the config keys: `stage_1a`, `stage_2`, `stage_3a`, `stage_3b`, `stage_4`, `stage_4_5`, `stage_6`, etc.
- Files in `parsers/` and `core/` need stage names assigned -- they may not map 1:1 to `run_stage_N()` functions.
- Existing tests (159+) must pass after each batch migration.

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- standard mechanical migration following the `call_llm()` interface defined in Phase 19.

</specifics>

<deferred>
## Deferred Ideas

- Async variant of `call_llm()` for `async_rate_limiter.py` -- only if async path becomes more widely used
- Assistants API abstraction -- only if more files adopt the Assistants pattern
- End-to-end sample CV pipeline validation -- Phase 21 (TEST-02)
- Bedrock provider implementation -- Phase 21 (BED-01, BED-02)

</deferred>

---

*Phase: 20-pipeline-migration*
*Context gathered: 2026-03-31*
