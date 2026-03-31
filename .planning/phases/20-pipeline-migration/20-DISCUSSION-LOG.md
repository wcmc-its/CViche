# Phase 20: Pipeline Migration - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-31
**Phase:** 20-pipeline-migration
**Areas discussed:** Migration batching, Non-chat-completions files, Verification strategy, Cleanup scope

---

## Migration Batching

| Option | Description | Selected |
|--------|-------------|----------|
| By directory | One plan per directory group (~4 plans, each self-contained and testable) | ✓ |
| Two big plans | Split into 'simple files' and 'complex files' | |
| Single plan | One plan migrates everything | |

**User's choice:** By directory (Recommended)
**Notes:** None

### Sub-question: Migration Order

| Option | Description | Selected |
|--------|-------------|----------|
| Parsers first | All 10 parsers follow same pattern, builds confidence. Then segmentation -> core -> stages. | ✓ |
| Stages first | Start with main pipeline stages as primary execution path | |
| You decide | Claude picks optimal order | |

**User's choice:** Parsers first (Recommended)

### Sub-question: Commit Granularity

| Option | Description | Selected |
|--------|-------------|----------|
| One commit per plan | Each directory batch is a single atomic commit | ✓ |
| One commit per file | Every migrated file gets its own commit (46 commits) | |
| You decide | Claude picks per plan | |

**User's choice:** One commit per plan (Recommended)

### Sub-question: Migration Comments

| Option | Description | Selected |
|--------|-------------|----------|
| Clean swap, no comments | Just replace import and call. Git history documents migration. | ✓ |
| Brief migration comment | One-liner comment at top of each file | |

**User's choice:** Clean swap, no comments (Recommended)

---

## Non-chat-completions Files

### Sub-question: Assistants API Files

| Option | Description | Selected |
|--------|-------------|----------|
| Leave as exceptions | Mark as known exceptions -- fundamentally different API | ✓ |
| Wrap in separate helper | Create call_assistants() alongside call_llm() | |
| Deprecate Assistants path | Remove code entirely if unused | |

**User's choice:** Leave as exceptions (Recommended)
**Notes:** 2 files use Assistants API (assistants_hierarchy_extractor.py, direct_file_hierarchy_extractor.py)

### Sub-question: Async Rate Limiter

| Option | Description | Selected |
|--------|-------------|----------|
| Leave as exception | Infrastructure code, not a pipeline stage. Legitimately needs AsyncOpenAI. | ✓ |
| Create async call_llm variant | Add async_call_llm() to llm_client.py | |
| You decide | Claude evaluates usage | |

**User's choice:** Leave as exception (Recommended)
**Notes:** async_rate_limiter.py is the only async file

---

## Verification Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Tests + grep check | Run test suite after each plan. Final grep confirms zero imports. E2E is Phase 21. | ✓ |
| End-to-end after each batch | Full sample CV pipeline after each directory migration | |
| Tests only at the end | Migrate everything then test once | |

**User's choice:** Tests + grep check (Recommended)

### Sub-question: Migration Audit Step

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, include audit step | Final plan includes grep-based audit confirming zero imports minus 3 exceptions | ✓ |
| No, tests are enough | Tests and per-plan verification sufficient | |

**User's choice:** Yes, include audit step (Recommended)

---

## Cleanup Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Remove redundant code | Remove local client init, retry loops, model constants. Keep changes LLM-focused. | ✓ |
| Minimal swap only | Only change import and call site. Leave dead code. | |
| Full modernization | Beyond LLM swap, refactor related patterns | |

**User's choice:** Remove redundant code (Recommended)

### Sub-question: Hardcoded Model Strings

| Option | Description | Selected |
|--------|-------------|----------|
| Remove hardcoded models | Let call_llm() use config-driven model. Hardcoded strings become dead code. | ✓ |
| Keep as fallback defaults | Pass as kwarg to call_llm() as belt-and-suspenders | |
| You decide per file | Claude evaluates per file | |

**User's choice:** Remove hardcoded models (Recommended)

### Sub-question: Per-file Cost Calculations

| Option | Description | Selected |
|--------|-------------|----------|
| Remove per-file cost code | call_llm() returns cost in response dict. Use result['cost']. | ✓ |
| Keep per-file cost code | Leave as cross-check against call_llm() cost tracking | |

**User's choice:** Remove per-file cost code (Recommended)

---

## Claude's Discretion

- Exact migration mechanics per file (adapting specific call patterns to call_llm())
- Whether to refactor function signatures structured around the old call pattern
- How to handle files with multiple LLM calls
- prompt_ab_tester.py and prompt_logger.py migration approach

## Deferred Ideas

- Async variant of call_llm() -- only if async usage grows
- Assistants API abstraction -- only if more files adopt Assistants pattern
- E2E sample CV validation -- Phase 21 (TEST-02)
- Bedrock provider -- Phase 21 (BED-01, BED-02)
