# Phase 20: Pipeline Migration - Research

**Researched:** 2026-03-31
**Domain:** Mechanical migration of OpenAI SDK calls to centralized `call_llm()` client
**Confidence:** HIGH

## Summary

Phase 20 is a mechanical migration: replace direct `from openai import OpenAI` / `client.chat.completions.create()` patterns with `from unified_pipeline.llm_client import call_llm` across 43 pipeline files. The Phase 19 foundation (`llm_client.py`, `config.py`, `llm_config.yaml`) is complete and well-tested (164 tests, 159 passing).

The codebase has exactly **43 files** needing migration (47 total with OpenAI imports minus `llm_client.py` itself and 3 documented exceptions). All files follow one of four well-understood patterns: (1) module-level `client = OpenAI()` with simple `client.chat.completions.create()` calls, (2) function-local `client = OpenAI()` instantiation, (3) class-based `self.client = OpenAI()`, and (4) function-local import `from openai import OpenAI`. Cleanup scope includes removing hand-rolled retry loops, local `MODEL_PRICING` dicts, local `calculate_cost()` functions, and hardcoded model strings.

**Primary recommendation:** Batch migration by directory (parsers -> segmentation -> core -> stages+cv_parser+validators) as specified in CONTEXT.md decisions D-01/D-02, with one critical concern to address: the `prompt_logger.log_prompt_response()` function expects raw OpenAI response objects (uses `hasattr(response, 'choices')`), but `call_llm()` returns a normalized dict. Files using prompt_logger need to either (a) pass the `call_llm()` result dict to a logger that can handle dicts, or (b) skip `log_prompt_response` for migrated calls since `call_llm()` already tracks latency/tokens/cost.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Migrate by directory group, one plan per group: parsers (10 files) -> segmentation (12 files, minus 2 exceptions) -> core (9 files, minus 1 exception) -> stages + cv_parser + validators (16 files). ~4 plans.
- **D-02:** Parsers first -- they all follow the exact same `client.chat.completions.create()` pattern, building confidence before tackling more complex files.
- **D-03:** One atomic commit per plan (per directory batch). Not per-file.
- **D-04:** Clean swap with no migration comments. Git history documents the change.
- **D-05:** `segmentation/assistants_hierarchy_extractor.py` and `segmentation/direct_file_hierarchy_extractor.py` are documented exceptions -- they use the OpenAI Assistants API (threads, file uploads, polling) which is fundamentally different from chat completions. Leave as-is.
- **D-06:** `core/async_rate_limiter.py` is a documented exception -- it uses `AsyncOpenAI` for concurrency. `call_llm()` is synchronous. Leave as-is.
- **D-07:** The "zero direct OpenAI imports" success criterion applies to all pipeline files EXCEPT these 3 documented exceptions and `llm_client.py` itself.
- **D-08:** Run existing test suite after each plan. After the final plan, grep confirms zero direct OpenAI imports in pipeline files (excluding the 3 exceptions).
- **D-09:** Final plan includes a migration audit step: grep-based confirmation of zero remaining imports, with the 3 exception files explicitly documented.
- **D-10:** End-to-end sample CV pipeline run is a Phase 21 concern (TEST-02), not Phase 20.
- **D-11:** Remove redundant code alongside each migration: local `client = OpenAI()` initialization, hand-rolled retry loops (call_llm handles retries per D-13 from Phase 19), and related dead code. Keep changes focused on LLM-related code only.
- **D-12:** Remove all hardcoded model strings (e.g., `'gpt-4o-mini'`, `'gpt-4o'`). Let `call_llm()` use config-driven model selection per stage (D-04, D-06 from Phase 19).
- **D-13:** Remove per-file cost calculation code. `call_llm()` returns cost in the normalized response dict (D-11 from Phase 19). Use `result['cost']` instead.

### Claude's Discretion
- Exact migration mechanics per file (how to adapt each file's specific call pattern to `call_llm()`)
- Whether to refactor function signatures that were structured around the old call pattern
- How to handle files with multiple LLM calls (multiple `call_llm()` invocations vs. restructuring)
- `prompt_ab_tester.py` and `prompt_logger.py` migration approach (tooling files, not pipeline stages)

### Deferred Ideas (OUT OF SCOPE)
- Async variant of `call_llm()` for `async_rate_limiter.py` -- only if async path becomes more widely used
- Assistants API abstraction -- only if more files adopt the Assistants pattern
- End-to-end sample CV pipeline validation -- Phase 21 (TEST-02)
- Bedrock provider implementation -- Phase 21 (BED-01, BED-02)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| LLM-03 | All 46 pipeline files that call OpenAI directly are migrated to use the centralized LLM client | Actual count is 43 files to migrate (47 with OpenAI imports minus llm_client.py and 3 exceptions). Migration patterns for all 4 code patterns documented below. Grep-based verification command identified. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| unified_pipeline.llm_client | N/A (local) | `call_llm()` centralized LLM interface | Phase 19 deliverable, already tested, handles retry/cost/config |
| unified_pipeline.config | N/A (local) | `get_stage_config()` for per-stage model resolution | Phase 19 deliverable, YAML-driven config with env var overrides |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | (already installed) | Test runner for 164 existing tests | Run after each plan to verify no regressions |

No new dependencies needed. This phase consumes existing infrastructure only.

## Architecture Patterns

### The `call_llm()` Interface (target state)

```python
# Source: src/unified_pipeline/llm_client.py
from unified_pipeline.llm_client import call_llm

result = call_llm(
    stage="stage_3b",                           # Stage name for config lookup
    messages=[{"role": "user", "content": "..."}],  # Standard messages list
    response_format={"type": "json_object"},     # Optional, passed through to SDK
    # Optional overrides (rarely needed):
    # temperature=0.5, model="gpt-4o", max_tokens=500
)
# result is a dict:
# {
#   "content": "...",        # LLM response text
#   "prompt_tokens": 100,
#   "completion_tokens": 50,
#   "total_tokens": 150,
#   "cost": 0.0001,
#   "model": "gpt-4o-mini",
#   "provider": "openai",
#   "finish_reason": "stop",
#   "latency_ms": 823,
# }
```

### Migration Pattern Categories

**Pattern 1: Module-level client, simple call (26 files -- all parsers, most segmentation)**
```python
# BEFORE:
from openai import OpenAI
client = OpenAI()
# ...
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=messages,
    response_format=response_format,
    temperature=0.1,
    max_tokens=300
)
content = response.choices[0].message.content
usage = response.usage

# AFTER:
from unified_pipeline.llm_client import call_llm
# ...
result = call_llm(
    stage="parser_honors",  # new stage name
    messages=messages,
    response_format=response_format,
)
content = result["content"]
# usage available as result["prompt_tokens"], result["completion_tokens"], etc.
```

**Pattern 2: Function-local client instantiation (8 files -- stages, cv_parser)**
```python
# BEFORE:
def some_function():
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(model="gpt-5.1", ...)
    return response.choices[0].message.content

# AFTER:
from unified_pipeline.llm_client import call_llm  # top of file

def some_function():
    result = call_llm(stage="stage_6", messages=[...])
    return result["content"]
```

**Pattern 3: Class-based client (4 files -- cv_parser, validators)**
```python
# BEFORE:
class DataStructurer:
    def __init__(self, use_llm=True):
        if use_llm:
            self.client = OpenAI()

    def _method(self):
        response = self.client.chat.completions.create(...)

# AFTER:
from unified_pipeline.llm_client import call_llm

class DataStructurer:
    def __init__(self, use_llm=True):
        self.use_llm = use_llm
        # No client init needed -- call_llm handles it

    def _method(self):
        result = call_llm(stage="cv_parser_structurer", messages=[...])
```

**Pattern 4: Files with hand-rolled retry loops (2 files)**
```python
# BEFORE (chunked_chat_hierarchy_extractor.py):
max_retries = 5
for attempt in range(max_retries):
    try:
        response = client.chat.completions.create(...)
        return response.choices[0].message.content
    except RateLimitError as e:
        time.sleep(wait_time)

# AFTER:
result = call_llm(stage="segmentation_hierarchy", messages=[...])
return result["content"]
# Retries handled internally by call_llm (3 retries with exponential backoff)
```

### Stage Name Convention

Files that don't map to existing `stage_N` names need new stage keys. These will use the default config unless overridden in `llm_config.yaml`.

| Directory | Stage Name Pattern | Examples |
|-----------|-------------------|----------|
| `parsers/` | `parser_{type}` | `parser_education`, `parser_honors`, `parser_grants` |
| `segmentation/` | `segmentation_{function}` | `segmentation_word`, `segmentation_hierarchy`, `segmentation_entry` |
| `core/` | `core_{function}` | `core_personal_info`, `core_taxonomy`, `core_recovery` |
| `cv_parser/` | `cv_parser_{function}` | `cv_parser_classifier`, `cv_parser_structurer` |
| `validators/` | `validator_{type}` | `validator_llm` |
| `stage_*.py` | Match file: `stage_2`, `stage_3a`, `stage_4`, etc. | `stage_2`, `stage_3a`, `stage_4_5`, `stage_6` |

All new stage names default to gpt-4o-mini/temperature 0 from the YAML default block. No YAML changes needed unless specific files need different models.

### Prompt Logger Compatibility

**Critical concern:** `log_prompt_response()` in `core/prompt_logger.py` uses `hasattr(response, 'choices')` to extract data from OpenAI response objects. After migration, `call_llm()` returns a dict, not an OpenAI response object.

**Resolution approach (Claude's discretion):**
1. After calling `call_llm()`, the `result` dict contains all the data `log_prompt_response` would extract (`content`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `model`, `finish_reason`, `latency_ms`).
2. Two options:
   - **(Recommended) Drop `log_prompt_response` calls from migrated files.** The `call_llm()` function already provides all the data prompt_logger would capture. `log_prompt_before_call()` still works (it only takes messages and params). The response data is available in the `result` dict if needed for logging elsewhere.
   - **Adapt `log_prompt_response` to accept dicts.** Add a dict-handling branch: `if isinstance(response, dict): ...`. This is more work for marginal benefit.
3. `log_prompt_before_call()` remains compatible since it only takes messages, model string, temperature, etc. -- all available before calling `call_llm()`. However, the `model` param to `log_prompt_before_call` was previously hardcoded (e.g., `model="gpt-4o-mini"`). After migration, the model comes from config. Either:
   - Remove `log_prompt_before_call` calls too (simplest), or
   - Retrieve the model from config first via `get_stage_config(stage)["model"]` for logging accuracy.

### Files Per Batch (Exact Migration Inventory)

**Batch 1: Parsers (10 files)**
All Pattern 1 (module-level client, single LLM call per parse function):
1. `parsers/certifications_parser.py`
2. `parsers/education_parser.py`
3. `parsers/grants_parser.py`
4. `parsers/honors_parser.py`
5. `parsers/licensure_parser.py`
6. `parsers/memberships_parser.py`
7. `parsers/mentoring_parser.py`
8. `parsers/positions_parser.py`
9. `parsers/publications_parser.py`
10. `parsers/service_parser.py`

All use: `from openai import OpenAI`, `client = OpenAI()` at module level, single `client.chat.completions.create()` call, `json_schema` response format, `log_prompt_before_call`/`log_prompt_response`. Token usage captured into `result['token_usage']` dict.

**Batch 2: Segmentation (10 files -- 12 minus 2 exceptions)**
Mix of Pattern 1 and Pattern 4:
1. `segmentation/chat_completions_hierarchy_extractor.py` -- Pattern 1
2. `segmentation/chunked_chat_hierarchy_extractor.py` -- **Pattern 4** (has hand-rolled retry loop + RateLimitError import)
3. `segmentation/entry_validator.py` -- Pattern 1
4. `segmentation/header_validator.py` -- Pattern 1
5. `segmentation/pdf_vision.py` -- Pattern 1
6. `segmentation/signature_based_segmentation.py` -- Pattern 1
7. `segmentation/stage2_entry_extractor.py` -- Pattern 1
8. `segmentation/word_chunked.py` -- Pattern 1
9. `segmentation/word_delimited.py` -- Pattern 1
10. `segmentation/word_original.py` -- Pattern 1

Exceptions (NOT migrated): `assistants_hierarchy_extractor.py`, `direct_file_hierarchy_extractor.py`

**Batch 3: Core (8 files -- 9 minus 1 exception)**
Mix of all patterns, most complex files:
1. `core/candidate_surfacer.py` -- Pattern 1, uses prompt_logger, has token tracking
2. `core/extraction_recovery.py` -- Pattern 1, uses prompt_logger
3. `core/personal_info_extractor.py` -- Pattern 1, uses prompt_logger
4. `core/prompt_ab_tester.py` -- Pattern 1 but **tooling file** (not pipeline stage), replays prompts with different params. Needs special handling per Claude's discretion.
5. `core/repair_segmentation.py` -- Pattern 2 (function-local import)
6. `core/section_extraction_orchestrator.py` -- Pattern 2 (function-local import)
7. `core/taxonomy_mapper_v2.py` -- **Complex:** Pattern 1, multiple LLM calls, local `MODEL_PRICING` and `calculate_cost()`, 6 separate `client.chat.completions.create()` calls
8. `core/taxonomy_mapper.py` -- Pattern 1, uses prompt_logger

Exception (NOT migrated): `core/async_rate_limiter.py`

**Batch 4: Stages + cv_parser + validators (15 files)**
Most complex batch with largest files:
1. `stage_2_entry_extraction.py` -- Pattern 2
2. `stage_2a_delimiter_detector.py` -- Pattern 2, uses prompt_logger
3. `stage_3a_header_taxonomy_mapper.py` -- Pattern 2, uses prompt_logger
4. `stage_3b_entry_classifier.py` -- Pattern 2, **3 separate LLM call sites**
5. `stage_4_field_extractor.py` -- Pattern 1, **has local `MODEL_PRICING` and `calculate_cost()`**, 4 LLM call sites
6. `stage_4_5_research_summary.py` -- Pattern 2 (uses `get_client()` helper), 2 LLM calls
7. `stage_5b_institution_enrichment.py` -- Pattern 2 (try/except import)
8. `stage_5c_teaching_formatter.py` -- Pattern 2 (try/except import)
9. `stage_5d_citation_formatter.py` -- Pattern 2 (try/except import)
10. `stage_6_word_template.py` -- Pattern 2, **2 function-local imports at different points in a 7000+ line file**
11. `cv_parser/data_structurer.py` -- Pattern 3 (class-based)
12. `cv_parser/llm_section_evaluator.py` -- Pattern 2 + accepts client param, shared client in `evaluate_batch()`
13. `cv_parser/phased_extractor.py` -- Pattern 2 (deep in a 1600+ line file)
14. `cv_parser/section_classifier.py` -- Pattern 3 (class-based)
15. `validators/llm_validator.py` -- Pattern 3 (class-based), has local `_calculate_cost()`

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry logic | Per-file retry loops with `time.sleep()` | `call_llm()` built-in retry (3 retries, exponential backoff) | Already handles RateLimitError, APITimeoutError, APIConnectionError, InternalServerError |
| Cost calculation | Per-file `MODEL_PRICING` dicts and `calculate_cost()` functions | `result["cost"]` from `call_llm()` return dict | Centralized pricing in `config.py`, automatically updated |
| Client instantiation | `client = OpenAI()` at module or function level | `call_llm()` lazy-initializes a singleton client | Avoids import-time side effects, single connection pool |
| Model selection | Hardcoded `model="gpt-4o-mini"` in each file | Config-driven via `get_stage_config(stage)` | Per-stage overrides, env var overrides, single YAML file |

**Key insight:** After migration, each file's LLM interaction reduces to a single `call_llm()` invocation per LLM call. All infrastructure (retry, cost, model selection, client lifecycle) is handled centrally.

## Common Pitfalls

### Pitfall 1: prompt_logger Compatibility
**What goes wrong:** `log_prompt_response(log_id, response, ...)` expects an OpenAI response object with `.choices`, `.usage`, `.model` attributes. After migration, the "response" is a dict from `call_llm()`.
**Why it happens:** The prompt_logger was written to work with raw SDK objects.
**How to avoid:** Either drop `log_prompt_response` calls (recommended -- `call_llm` already captures all response data) or adapt the logger to handle dicts.
**Warning signs:** `AttributeError: 'dict' object has no attribute 'choices'` in prompt logging.

### Pitfall 2: Token Usage Dict Shape Changes
**What goes wrong:** Many files construct `result['token_usage'] = {'prompt_tokens': response.usage.prompt_tokens, ...}`. After migration, token data is in the flat `result` dict, not nested under `.usage`.
**Why it happens:** The old pattern extracts from OpenAI response object attributes; the new pattern uses dict keys.
**How to avoid:** Replace `response.usage.prompt_tokens` with `result["prompt_tokens"]`. If the file builds a `token_usage` sub-dict for its return value, construct it from `result["prompt_tokens"]`, `result["completion_tokens"]`, `result["total_tokens"]`.
**Warning signs:** KeyError or AttributeError when accessing token counts.

### Pitfall 3: Stage Name for Non-Stage Files
**What goes wrong:** `call_llm()` requires a `stage` parameter for config resolution. Parser files, core utilities, and validators don't have natural stage names.
**Why it happens:** The config system was designed around `stage_N` naming.
**How to avoid:** Use descriptive stage names (e.g., `parser_education`, `core_taxonomy`). These fall through to the default config (gpt-4o-mini, temp 0) which matches what these files already use. No YAML changes needed.
**Warning signs:** None -- unknown stage names use defaults silently.

### Pitfall 4: Files with Multiple LLM Call Sites
**What goes wrong:** `taxonomy_mapper_v2.py` has 6 LLM calls, `stage_3b_entry_classifier.py` has 3, `stage_4_field_extractor.py` has 4. Each call may use different parameters.
**Why it happens:** Complex pipeline stages make multiple LLM calls for different sub-tasks.
**How to avoid:** Each `client.chat.completions.create()` becomes its own `call_llm()`. Use the same stage name for all calls within a file (config provides the model). If a specific call needs different temperature or max_tokens, pass as kwargs to `call_llm()`.
**Warning signs:** Missed call sites in large files.

### Pitfall 5: Hardcoded Model Strings in Special Cases
**What goes wrong:** Some files hardcode `model="gpt-5.1"` or `model="gpt-4o"` for specific calls (e.g., `stage_6_word_template.py` line 1319 uses `gpt-5.1`). Per D-12, these should be removed.
**Why it happens:** Some calls were intentionally upgraded to premium models for quality.
**How to avoid:** Per D-12, remove hardcoded model strings. If a specific sub-task needs a premium model, add a stage override in `llm_config.yaml` rather than hardcoding. For now, use the default config and note any model changes needed.
**Warning signs:** Output quality regression if a call previously used gpt-4o but now gets gpt-4o-mini.

### Pitfall 6: try/except OpenAI Import Guards
**What goes wrong:** Several files (stage_5c, stage_5b, stage_5d) use `try: from openai import OpenAI; OPENAI_AVAILABLE = True except ImportError: OPENAI_AVAILABLE = False` patterns.
**Why it happens:** These files have fallback behavior when OpenAI is not installed.
**How to avoid:** Replace with `from unified_pipeline.llm_client import call_llm`. If the file uses `OPENAI_AVAILABLE` to guard LLM calls, keep the guard but check for `call_llm` availability instead, or simply remove the guard since `call_llm` is always available in this codebase.
**Warning signs:** Unused `OPENAI_AVAILABLE` flag after migration.

## Code Examples

### Parser Migration (Canonical Pattern)

```python
# Source: Verified pattern from parsers/honors_parser.py lines 13-119

# BEFORE (current state):
from openai import OpenAI
client = OpenAI()

def parse_honor_entry(text: str) -> Dict[str, Any]:
    messages = [{"role": "system", "content": "..."}, {"role": "user", "content": text}]
    response_format = {"type": "json_schema", "json_schema": {"name": "honor_entry", "schema": HONOR_SCHEMA, "strict": True}}

    log_id = log_prompt_before_call(messages=messages, model="gpt-4o-mini", temperature=0.1, ...)
    start_time = time.time()
    response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, response_format=response_format, temperature=0.1, max_tokens=300)
    elapsed_time = time.time() - start_time
    log_prompt_response(log_id, response, "honor_parsing", elapsed_time)

    honor = json.loads(response.choices[0].message.content)
    honor['token_usage'] = {
        'prompt_tokens': response.usage.prompt_tokens,
        'completion_tokens': response.usage.completion_tokens,
        'total_tokens': response.usage.total_tokens
    }
    return honor

# AFTER (migrated):
from unified_pipeline.llm_client import call_llm

def parse_honor_entry(text: str) -> Dict[str, Any]:
    messages = [{"role": "system", "content": "..."}, {"role": "user", "content": text}]
    response_format = {"type": "json_schema", "json_schema": {"name": "honor_entry", "schema": HONOR_SCHEMA, "strict": True}}

    result = call_llm(stage="parser_honors", messages=messages, response_format=response_format)

    honor = json.loads(result["content"])
    honor['token_usage'] = {
        'prompt_tokens': result["prompt_tokens"],
        'completion_tokens': result["completion_tokens"],
        'total_tokens': result["total_tokens"]
    }
    return honor
```

### Retry Loop Removal (chunked_chat_hierarchy_extractor.py)

```python
# BEFORE (current state, lines 225-264):
from openai import OpenAI
from openai import RateLimitError
client = OpenAI()

max_retries = 5
base_delay = 1
for attempt in range(max_retries):
    try:
        response = client.chat.completions.create(model=model, messages=[...], temperature=0.0)
        return response.choices[0].message.content.strip()
    except RateLimitError as e:
        if attempt < max_retries - 1:
            wait_time = base_delay * (2 ** attempt)
            time.sleep(wait_time)
        else:
            raise

# AFTER:
from unified_pipeline.llm_client import call_llm

result = call_llm(stage="segmentation_hierarchy", messages=[...])
return result["content"].strip()
```

### Class-Based Migration (cv_parser/data_structurer.py)

```python
# BEFORE:
from openai import OpenAI

class DataStructurer:
    def __init__(self, use_llm=True):
        self.use_llm = use_llm
        if use_llm:
            self.client = OpenAI()

# AFTER:
from unified_pipeline.llm_client import call_llm

class DataStructurer:
    def __init__(self, use_llm=True):
        self.use_llm = use_llm
        # call_llm handles client lifecycle
```

### Local Cost Calculation Removal (stage_4_field_extractor.py)

```python
# BEFORE (lines 50-87 -- to be REMOVED):
MODEL_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    # ... more models
}

def calculate_cost(model, input_tokens, output_tokens):
    pricing = MODEL_PRICING.get(model, ...)
    return (input_tokens * pricing["input"] / 1_000_000) + (output_tokens * pricing["output"] / 1_000_000)

# AFTER: Delete MODEL_PRICING and calculate_cost entirely.
# Use result["cost"] from call_llm() instead.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Direct `OpenAI()` client per file | Centralized `call_llm()` | Phase 19 (2026-03-31) | Single client instance, config-driven model selection |
| Hardcoded model strings per file | YAML config with per-stage overrides | Phase 19 (2026-03-31) | Change model for any stage without code changes |
| Per-file retry loops | Built-in retry in `call_llm()` | Phase 19 (2026-03-31) | Consistent retry behavior across all stages |
| Per-file cost calculation | Centralized `calculate_cost()` in config.py | Phase 19 (2026-03-31) | Single pricing table, automatic with each call |

## Open Questions

1. **prompt_ab_tester.py migration approach**
   - What we know: This is a tooling file that replays logged prompts with different parameters (temperatures, models). It uses `client.chat.completions.create(**call_params)` where `call_params` are dynamically constructed from logged data.
   - What's unclear: Whether `call_llm()` can accommodate arbitrary parameter replay, or if this file needs a different approach.
   - Recommendation: Migrate to `call_llm()` with kwargs override. The `**kwargs` passthrough in `call_llm()` should handle arbitrary params. Use a generic stage name like `"tool_ab_tester"`.

2. **Stage names for parser/core files**
   - What we know: `get_stage_config()` gracefully handles unknown stage names (returns defaults). No YAML changes needed.
   - What's unclear: Whether any parser/core file currently relies on non-default model settings that would be lost.
   - Recommendation: All parsers currently hardcode `gpt-4o-mini` which matches the default. Core files also use `gpt-4o-mini` except `taxonomy_mapper_v2.py` which sometimes uses different models in specific calls. Stage-level YAML overrides can be added later if needed.

3. **Files with hardcoded non-default models**
   - What we know: `stage_6_word_template.py:1319` uses `gpt-5.1`, `stage_4_field_extractor.py:930` uses model from config but some calls may use different models.
   - What's unclear: Full list of calls that intentionally use premium models.
   - Recommendation: During migration, note any hardcoded model that differs from `gpt-4o-mini`. If a file consistently uses a premium model for all its calls, add a YAML stage override. If only specific sub-calls need it, pass `model=` as a kwarg to those `call_llm()` calls temporarily, and document for later YAML configuration.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (installed, no config file -- uses defaults) |
| Config file | None (uses pytest defaults) |
| Quick run command | `python3 -m pytest web_interface/backend/tests/ -q --tb=short` |
| Full suite command | `python3 -m pytest web_interface/backend/tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LLM-03 | Zero pipeline files import openai directly (excl. 3 exceptions + llm_client.py) | smoke | `grep -rl "from openai\|import openai" src/unified_pipeline/ --include="*.py" \| grep -v llm_client.py \| grep -v async_rate_limiter.py \| grep -v assistants_hierarchy_extractor.py \| grep -v direct_file_hierarchy_extractor.py` (expect empty output) | N/A (grep command) |
| LLM-03 | Existing test suite passes after migration | regression | `python3 -m pytest web_interface/backend/tests/ -q --tb=short` | Yes (164 tests, 159 pass) |

### Sampling Rate
- **Per plan commit:** `python3 -m pytest web_interface/backend/tests/ -q --tb=short` (fast: ~2s)
- **Per wave merge:** Full test suite + grep audit
- **Phase gate:** Full suite green + grep confirms zero remaining direct imports

### Wave 0 Gaps
None -- existing test infrastructure covers phase requirements. The 164 existing tests validate the LLM client and config system. Migration verification is grep-based (no new test files needed for LLM-03).

## Sources

### Primary (HIGH confidence)
- `src/unified_pipeline/llm_client.py` -- call_llm() signature, return shape, retry behavior (read directly)
- `src/unified_pipeline/config.py` -- get_stage_config() resolution, calculate_cost(), PRICING dict (read directly)
- `src/unified_pipeline/config/llm_config.yaml` -- Current config with stage overrides (read directly)
- `web_interface/backend/tests/test_llm_client.py` -- 13 tests covering call_llm behavior (read directly)
- `web_interface/backend/tests/test_llm_config.py` -- 10 tests covering config resolution (read directly)
- All 47 files with OpenAI imports -- grep'd and representative samples read directly

### Secondary (MEDIUM confidence)
- `.planning/phases/19-abstraction-foundation/19-CONTEXT.md` -- Phase 19 design decisions (D-10 through D-13)
- `.planning/phases/20-pipeline-migration/20-CONTEXT.md` -- Phase 20 user decisions (D-01 through D-13)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All code is local, read directly, no external dependencies
- Architecture: HIGH - Four migration patterns identified from reading actual source files
- Pitfalls: HIGH - prompt_logger compatibility issue discovered by reading actual code; token_usage dict shape verified from source

**Research date:** 2026-03-31
**Valid until:** 2026-04-30 (stable -- no external dependencies changing)
