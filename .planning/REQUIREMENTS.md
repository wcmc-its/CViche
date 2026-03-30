# Requirements: CViche

**Defined:** 2026-03-29
**Core Value:** Faculty can upload a CV and get back a correctly formatted WCM document without manual reformatting

## v1.5 Requirements

### LLM Abstraction

- [ ] **LLM-01**: A centralized LLM client module exists that all pipeline stages use instead of direct OpenAI calls
- [ ] **LLM-02**: The LLM client supports chat completions with messages, model selection, temperature, and JSON response format
- [ ] **LLM-03**: All 46 pipeline files that call OpenAI directly are migrated to use the centralized LLM client
- [ ] **LLM-04**: The LLM client tracks token usage and cost per call, compatible with the existing LLMUsage model

### Bedrock Provider

- [ ] **BED-01**: AWS Bedrock is supported as an LLM provider via boto3 bedrock-runtime (Claude, Llama, Mistral models)
- [ ] **BED-02**: Bedrock authentication uses IAM credentials (env vars or instance roles) — no hardcoded keys

### Configuration

- [ ] **CFG-01**: A deployment config file sets the global default LLM provider and model (OpenAI remains default)
- [ ] **CFG-02**: Individual pipeline stages can override the global default with a stage-specific model
- [ ] **CFG-03**: The pipeline runs correctly with only `OPENAI_API_KEY` set (no AWS credentials required)

### Testing

- [ ] **TEST-01**: Unit tests verify the abstraction layer works with both OpenAI and Bedrock providers
- [ ] **TEST-02**: The sample CV pipeline completes successfully using the default (OpenAI) configuration

## Out of Scope

| Feature | Reason |
|---------|--------|
| Web UI model picker | Deployment config only per user requirement |
| Streaming LLM responses | Pipeline stages use synchronous calls; streaming adds complexity without benefit here |
| Additional providers (Azure, Google) | Bedrock + OpenAI covers the immediate need; abstraction layer makes adding more easy later |
| Prompt rewriting for different models | Existing prompts should work across providers; tune later if needed |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| LLM-01 | TBD | Pending |
| LLM-02 | TBD | Pending |
| LLM-03 | TBD | Pending |
| LLM-04 | TBD | Pending |
| BED-01 | TBD | Pending |
| BED-02 | TBD | Pending |
| CFG-01 | TBD | Pending |
| CFG-02 | TBD | Pending |
| CFG-03 | TBD | Pending |
| TEST-01 | TBD | Pending |
| TEST-02 | TBD | Pending |

**Coverage:**
- v1.5 requirements: 11 total
- Mapped to phases: 0/11 (pending roadmap)
- Unmapped: 11

---
*Requirements defined: 2026-03-29*
