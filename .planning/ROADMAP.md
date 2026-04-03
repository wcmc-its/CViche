# Roadmap: CViche

## Milestones

- v1.0 Public Release - Phases 1-3 (shipped 2026-03-23)
- v1.1 Web Interface UX - Phases 4-6 (shipped 2026-03-24)
- v1.2 Auth & Access Control - Phases 7-11 (shipped 2026-03-26)
- v1.3 Code Quality & Security - Phases 12-15 (shipped 2026-03-27)
- v1.4 Docs & UX Polish - Phases 16-18 ([archived](milestones/v1.4-ROADMAP.md), shipped 2026-03-28)
- v1.5 LLM Provider Abstraction - Phases 19-21 (in progress)

## v1.5 LLM Provider Abstraction

**Milestone Goal:** Pipeline stages can run on any supported LLM provider (OpenAI, AWS Bedrock), configurable per deployment, without breaking existing OpenAI-only setups.

## Phases

- [x] **Phase 19: Abstraction Foundation** - Config system, centralized LLM client, and cost tracking layer (completed 2026-03-31)
- [x] **Phase 20: Pipeline Migration** - Migrate all 43 pipeline files from direct OpenAI calls to the LLM client (completed 2026-04-03)
- [ ] **Phase 21: Bedrock Provider & Validation** - AWS Bedrock provider implementation and end-to-end test coverage

## Phase Details

### Phase 19: Abstraction Foundation
**Goal**: A centralized LLM client exists with provider config, model selection, and cost tracking -- ready for pipeline files to adopt
**Depends on**: Phase 18
**Requirements**: CFG-01, CFG-02, CFG-03, LLM-01, LLM-02, LLM-04
**Success Criteria** (what must be TRUE):
  1. A deployment config file exists that sets global default provider and model, with OpenAI as default
  2. The config supports per-stage model overrides (e.g., stage_4 uses gpt-4o while others use gpt-4o-mini)
  3. A centralized LLM client module accepts messages, model, temperature, and JSON response format and returns completions
  4. Each LLM call records token usage and cost to the existing LLMUsage model with a provider field
  5. Running the pipeline with only OPENAI_API_KEY set (no AWS credentials) works without errors
**Plans:** 3/3 plans complete
Plans:
- [x] 19-01-PLAN.md -- YAML config system, PRICING restructure, get_stage_config()
- [x] 19-02-PLAN.md -- LLMUsage provider column, Alembic migration, WebSocket cost events
- [x] 19-03-PLAN.md -- Centralized call_llm() function with retries and normalized response

### Phase 20: Pipeline Migration
**Goal**: Every pipeline file uses the centralized LLM client -- no direct OpenAI imports remain in pipeline code
**Depends on**: Phase 19
**Requirements**: LLM-03
**Success Criteria** (what must be TRUE):
  1. Zero pipeline files import openai directly (all go through the LLM client)
  2. The sample CV pipeline completes with identical output quality after migration
  3. Per-stage model overrides from config are respected during pipeline execution
**Plans:** 4/4 plans complete
Plans:
- [x] 20-01-PLAN.md -- Migrate 10 parser files to call_llm()
- [x] 20-02-PLAN.md -- Migrate 10 segmentation files to call_llm() (excl. 2 Assistants API exceptions)
- [x] 20-03-PLAN.md -- Migrate 8 core files to call_llm(), update prompt_logger for dict responses
- [x] 20-04-PLAN.md -- Migrate 15 stage/cv_parser/validator files, final migration audit

### Phase 21: Bedrock Provider & Validation
**Goal**: AWS Bedrock works as an alternative LLM provider, and both providers are validated with automated tests
**Depends on**: Phase 20
**Requirements**: BED-01, BED-02, TEST-01, TEST-02
**Success Criteria** (what must be TRUE):
  1. Switching the global provider to "bedrock" in config causes pipeline stages to call AWS Bedrock instead of OpenAI
  2. Bedrock authentication uses IAM credentials from environment or instance roles (no hardcoded keys)
  3. Unit tests verify the abstraction layer dispatches correctly to both OpenAI and Bedrock providers
  4. The sample CV pipeline completes end-to-end using the default OpenAI configuration
**Plans:** 2 plans
Plans:
- [ ] 21-01-PLAN.md -- Bedrock provider implementation: pricing, _call_bedrock(), Converse API, JSON validation
- [ ] 21-02-PLAN.md -- Test coverage: Bedrock unit tests, JSON validation tests, E2E pipeline smoke test

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 19. Abstraction Foundation | v1.5 | 3/3 | Complete    | 2026-03-31 |
| 20. Pipeline Migration | v1.5 | 4/4 | Complete    | 2026-04-03 |
| 21. Bedrock Provider & Validation | v1.5 | 0/2 | Not started | - |
