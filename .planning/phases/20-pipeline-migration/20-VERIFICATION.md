---
phase: 20-pipeline-migration
verified: 2026-03-31T00:00:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
---

# Phase 20: Pipeline Migration Verification Report

**Phase Goal:** Migrate entire pipeline from direct OpenAI SDK calls to centralized call_llm() abstraction layer
**Verified:** 2026-03-31
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | Zero parser files import openai directly | VERIFIED | `grep -rl "from openai" src/unified_pipeline/parsers/` returns empty |
| 2  | All 10 parsers call call_llm() with correct stage prefix | VERIFIED | 10/10 files matched `call_llm(stage="parser_*"`; 10 files confirmed by grep count |
| 3  | No hardcoded model strings in parsers | VERIFIED | `grep -rn "gpt-4o\|gpt-5"` on parsers returns empty |
| 4  | No prompt_logger calls remain in parsers | VERIFIED | `grep -rn "log_prompt_before_call\|log_prompt_response"` on parsers returns empty |
| 5  | Token usage dicts populated from result dict keys | VERIFIED | 24 matches for `result["prompt_tokens"]` etc. across parsers |
| 6  | Zero segmentation files (excl. 2 Assistants API exceptions) import openai directly | VERIFIED | Only assistants_hierarchy_extractor.py and direct_file_hierarchy_extractor.py remain — both are documented exceptions |
| 7  | Retry loop removed from chunked_chat_hierarchy_extractor.py | VERIFIED | `grep -rn "RateLimitError\|max_retries\|base_delay"` returns empty |
| 8  | Zero core files (excl. async_rate_limiter.py) import openai directly | VERIFIED | `grep -rl "from openai" src/unified_pipeline/core/ --include="*.py" \| grep -v async_rate_limiter.py` returns empty |
| 9  | prompt_logger.py handles dict responses from call_llm | VERIFIED | `isinstance(response, dict)` at line 196 |
| 10 | taxonomy_mapper_v2.py local MODEL_PRICING and calculate_cost() removed | VERIFIED | Both grep checks return empty for taxonomy_mapper_v2.py |
| 11 | taxonomy_mapper_v2.py LLM calls migrated (4 actual call sites) | VERIFIED | 4 call_llm() calls found at lines 1866, 2514, 2841, 3791 — plan estimated "6+" but SUMMARY confirms 4 was the actual count |
| 12 | prompt_ab_tester.py uses call_llm with no openai import | VERIFIED | No openai import; call_llm present |
| 13 | Zero stage_*.py files import openai directly | VERIFIED | `grep -rl "from openai" src/unified_pipeline/stage_*.py` returns empty |
| 14 | stage_4_field_extractor.py local MODEL_PRICING and calculate_cost() removed | VERIFIED | Both grep checks return empty |
| 15 | OPENAI_AVAILABLE guards removed from stage_5b/5c/5d | VERIFIED | `grep -rn "OPENAI_AVAILABLE"` on all three files returns empty |
| 16 | Class-based files no longer store self.client | VERIFIED | `grep -rn "self.client = OpenAI\|self\.client\.chat\."` on data_structurer, section_classifier, llm_validator returns empty |
| 17 | All 15 plan-04 files import call_llm | VERIFIED | Loop check over all 15 files confirms presence |
| 18 | Full pipeline audit: zero remaining direct OpenAI imports (excl. 4 documented exceptions) | VERIFIED | Only llm_client.py, async_rate_limiter.py, assistants_hierarchy_extractor.py, direct_file_hierarchy_extractor.py remain |
| 19 | Test suite passes with zero regressions | VERIFIED | 159 passed, 5 skipped — matches expectation |

**Score:** 9/9 truth categories verified (19 individual checks, all passed)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/unified_pipeline/parsers/honors_parser.py` | Migrated parser using call_llm | VERIFIED | Imports call_llm, calls `call_llm(stage="parser_honors", ...)` |
| `src/unified_pipeline/parsers/education_parser.py` | Migrated parser using call_llm | VERIFIED | Imports call_llm, calls `call_llm(stage="parser_education", ...)` |
| `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py` | Retry loop removed, call_llm used | VERIFIED | No RateLimitError/max_retries/base_delay; call_llm at line 219 |
| `src/unified_pipeline/segmentation/chat_completions_hierarchy_extractor.py` | Migrated using call_llm | VERIFIED | call_llm at line 126 with stage="segmentation_chat_hierarchy" |
| `src/unified_pipeline/core/taxonomy_mapper_v2.py` | 4 LLM calls migrated, local pricing removed | VERIFIED | 4 call_llm calls; no MODEL_PRICING or calculate_cost |
| `src/unified_pipeline/core/prompt_ab_tester.py` | Uses call_llm with kwargs | VERIFIED | Imports call_llm, no openai import |
| `src/unified_pipeline/core/prompt_logger.py` | Handles both OpenAI objects and dicts | VERIFIED | `isinstance(response, dict)` at line 196 |
| `src/unified_pipeline/stage_4_field_extractor.py` | Local pricing removed, 4 call sites migrated | VERIFIED | 4 call_llm calls; no MODEL_PRICING |
| `src/unified_pipeline/stage_6_word_template.py` | 2 function-local imports migrated | VERIFIED | 2 call_llm calls; no openai import |
| `src/unified_pipeline/validators/llm_validator.py` | Class-based client removed, local cost calc removed | VERIFIED | No self.client; no _calculate_cost |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/unified_pipeline/parsers/*.py` (10 files) | `src/unified_pipeline/llm_client.py` | `from unified_pipeline.llm_client import call_llm` | WIRED | All 10 parsers confirmed |
| `src/unified_pipeline/segmentation/*.py` (10 migrated files) | `src/unified_pipeline/llm_client.py` | `from unified_pipeline.llm_client import call_llm` | WIRED | All 10 segmentation files confirmed; 14 total call_llm() invocations found |
| `src/unified_pipeline/core/*.py` (8 migrated files) | `src/unified_pipeline/llm_client.py` | `from unified_pipeline.llm_client import call_llm` | WIRED | All 8 core files confirmed |
| `src/unified_pipeline/stage_*.py` + `cv_parser/*.py` + `validators/*.py` (15 files) | `src/unified_pipeline/llm_client.py` | `from unified_pipeline.llm_client import call_llm` | WIRED | All 15 files confirmed |
| Documented exceptions (3 files) | OpenAI SDK | direct import (permitted) | CORRECTLY EXCLUDED | assistants_hierarchy_extractor.py, direct_file_hierarchy_extractor.py (Assistants API), async_rate_limiter.py (AsyncOpenAI) |

---

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| LLM-03 | 20-01, 20-02, 20-03, 20-04 | All 46 pipeline files that call OpenAI directly are migrated to use the centralized LLM client | SATISFIED | 45 files use call_llm(); 4 documented exceptions confirmed; 0 unaccounted openai imports; 159 tests pass |

No orphaned requirements found. REQUIREMENTS.md maps only LLM-03 to Phase 20.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/unified_pipeline/segmentation/word_chunked_with_validation.py` | 22-23 | Hardcoded `"gpt-5.1"` and `"gpt-4o-mini"` as function parameter defaults | INFO | These parameters are passed to delegate functions that now use call_llm internally (word_chunked.py, entry_validator.py). The values are not used for direct API calls and do not bypass call_llm. File was not in any plan's migration list — not a blocker. |

No blocker anti-patterns found. No stub implementations. No migration comments (D-04 respected).

---

### Human Verification Required

None. All critical behaviors (OpenAI import removal, call_llm wiring, token usage dict structure, retry loop removal, local pricing removal, class-based client removal, test suite) are verifiable programmatically and all pass.

---

### Gaps Summary

No gaps. All must-haves across all four plans are verified.

**Clarification on taxonomy_mapper_v2.py call site count:** The plan estimated "6+" call sites but the SUMMARY and codebase both confirm 4. This is not a gap — the research estimate was conservative and the actual migration was complete (no remaining openai imports or client.chat.completions calls in that file).

**Clarification on prompt_logger.py grep hit:** `grep -rl "client\.chat\.completions"` returned prompt_logger.py because line 23 contains that string in a docstring comment, not functional code. No actual openai import exists in that file.

---

_Verified: 2026-03-31_
_Verifier: Claude (gsd-verifier)_
