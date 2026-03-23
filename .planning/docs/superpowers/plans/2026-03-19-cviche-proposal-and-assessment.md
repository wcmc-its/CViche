# CViche Proposal & Technical Assessment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce two documents — an internal technical assessment and a CIO one-pager — for deploying CViche at Weill Cornell Medicine.

**Architecture:** Research-then-write. The technical assessment requires gathering concrete facts from the codebase (model usage, pricing, OpenAI coupling, data flow). The CIO one-pager is written second, informed by assessment findings. Both are markdown documents.

**Tech Stack:** Markdown documents. Research involves reading Python source files, JSON configs, and existing documentation.

**Spec:** `docs/superpowers/specs/2026-03-19-cviche-proposal-and-assessment-design.md`

---

## File Structure

| File | Purpose |
|------|---------|
| `/Users/paulalbert/Dropbox/Projects/CViche - Planning/technical-assessment.md` | Internal technical assessment (candid) |
| `/Users/paulalbert/Dropbox/Projects/CViche - Planning/cviche-cio-proposal.md` | CIO one-pager (factual, operational) |

---

### Task 1: Gather Model & Cost Facts from Codebase

**Purpose:** Collect the concrete data points needed for Sections 1, 3, and 4 of the technical assessment. Every claim must be grounded in the actual codebase, not assumed.

**Files to read:**
- `run_full_pipeline.py:220-230` — default model setting
- `src/unified_pipeline/config.py:55-75` — DEFAULT_MODEL and PRICING dict (stale)
- `src/unified_pipeline/stage_4_field_extractor.py:50-90` — MODEL_PRICING dict (includes gpt-5.1)
- `web_interface/backend/app/pipeline/orchestrator.py:195-210` — web interface model default
- `src/unified_pipeline/stage_3a_header_taxonomy_mapper.py:385` — stage 3a model
- `src/unified_pipeline/stage_3b_entry_classifier.py:1777` — stage 3b model
- `src/unified_pipeline/stage_5d_citation_formatter.py:209,295` — stage 5d model
- `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py:24-25` — OpenAI imports

**Deliverable:** A structured set of facts (written as notes, not a final document) covering:
- Which model each stage actually uses (all gpt-5.1 in practice)
- The config.py vs run_full_pipeline.py discrepancy
- gpt-5.1 pricing from stage_4_field_extractor.py MODEL_PRICING
- Count of files importing `from openai import OpenAI` (20+ files)
- Whether any abstraction layer exists between stages and the OpenAI SDK (it doesn't)

- [ ] **Step 1: Read model defaults from pipeline runner and config**

Read `run_full_pipeline.py:220-230` and `src/unified_pipeline/config.py:55-75`. Document the discrepancy: config.py says `DEFAULT_MODEL = "gpt-4o-mini"` (line 58) while run_full_pipeline.py uses `model = "gpt-5.1"` (line 226).

- [ ] **Step 2: Read gpt-5.1 pricing from stage_4_field_extractor.py**

Read `src/unified_pipeline/stage_4_field_extractor.py:50-90`. The MODEL_PRICING dict at line 75 has gpt-5.1 pricing. Note: config.py PRICING (line 65) lacks gpt-5.1 and silently falls back to gpt-4o-mini pricing (line 139-140), meaning cost calculations elsewhere undercount.

- [ ] **Step 3: Count OpenAI SDK coupling**

Grep for `from openai import OpenAI` across `src/`. Count the files. This quantifies the vendor lock-in claim. Also check whether any stage uses an abstraction layer or calls OpenAI directly (they all call directly).

- [ ] **Step 4: Read web interface model config**

Read `web_interface/backend/app/pipeline/orchestrator.py:195-210`. Confirm `self.model = "gpt-5.1"` (line 202). Both CLI and web paths use the same model.

- [ ] **Step 5: Write research notes**

Compile findings into structured notes. These are working notes, not the final document. Include exact file paths and line numbers for every claim.

---

### Task 2: Map Data Flow for PII Analysis

**Purpose:** Document the exact path faculty PII takes through the system — from upload to API transmission to storage. This feeds Section 5 (Data Handling & PII Considerations) of the technical assessment.

**Files to read:**
- `web_interface/backend/app/api/upload.py` — how files are received and stored
- `web_interface/backend/app/pipeline/orchestrator.py:280-400` — how pipeline processes the file
- `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py:100-200` — how CV text is sent to OpenAI
- `src/unified_pipeline/stage_4_field_extractor.py:850-900` — how entries are sent to OpenAI
- `src/unified_pipeline/core/prompt_logger.py` — whether prompts/responses are logged to disk
- `web_interface/backend/app/database.py` — what gets stored in SQLite
- `web_interface/backend/app/models.py` — database schema (Run, Step, Log)

- [ ] **Step 1: Trace upload path**

Read `web_interface/backend/app/api/upload.py`. Document where uploaded files are saved (uploads/ directory) and what metadata is captured.

- [ ] **Step 2: Trace API transmission**

Read the segmentation and field extraction stages to identify exactly what text is sent to OpenAI. Is it the full CV text? Chunked? Are personal details (name, address, SSN-adjacent info) included in every API call or only certain stages?

- [ ] **Step 3: Trace local storage**

Read `src/unified_pipeline/core/prompt_logger.py`. If prompt logging is enabled, full prompts and responses (containing PII) are written to disk. Also check the pipeline output directories — intermediate JSON files contain extracted personal data.

- [ ] **Step 4: Check for data retention/deletion**

Grep for any cleanup, deletion, or retention logic. Check if there's any mechanism to purge processed CVs or their intermediate outputs.

- [ ] **Step 5: Write PII data flow notes**

Compile a data flow diagram in text: Upload → local storage → chunked text to OpenAI API → response stored locally → intermediate JSONs → final .docx. Note: no encryption at rest, no deletion policy, no BAA/DPA reference found.

---

### Task 3: Analyze Pipeline Architecture for Critique

**Purpose:** Build the evidence base for Sections 1 and 2 (Architecture Critique and Alternative Architecture Analysis). Count token usage patterns, identify which stages are LLM-dependent vs. deterministic, and assess what could be consolidated.

**Files to read:**
- `run_full_pipeline.py:91-93` — `get_stage_order()` for the full stage list
- `src/unified_pipeline/stage_1b_hierarchy_mapper.py` — deterministic stage (no LLM)
- `src/unified_pipeline/stage_2_entry_extraction.py:1-30` — check if LLM or deterministic
- `src/unified_pipeline/stage_5_pubmed_enrichment.py:1-30` — API-only stage
- `src/unified_pipeline/stage_5b_institution_enrichment.py:1-30` — API-only stage
- `src/unified_pipeline/stage_6_word_template.py:1-30` — deterministic stage
- `taxonomy_reference.md` — full taxonomy (needed to assess context window feasibility)
- `src/unified_pipeline/config/field_schemas_v1.1.json` — field schemas (needed for single-pass assessment)

- [ ] **Step 1: Categorize each stage as LLM, API, or deterministic**

For each stage file, grep for `from openai import OpenAI` AND for actual API call patterns (`client.chat.completions.create` or similar). A file may import OpenAI but only use it conditionally. Build a verified table. Do NOT rely on assumptions — check each file empirically.

Known from codebase exploration:
- Stages with OpenAI imports: 1a, 2, 3a, 3b, 4, 4.5, 5c, 5d (confirmed LLM)
- Stage 5b (`stage_5b_institution_enrichment.py`) imports OpenAI at line 34 — verify whether it uses LLM calls or only ROR API
- Stage 6 (`stage_6_word_template.py`) imports OpenAI at lines 1297 and 6969 — verify whether these are primary or fallback/conditional LLM calls
- Stage 5 (`stage_5_pubmed_enrichment.py`) — verify no LLM usage (expected: NCBI API only)
- Stage 1b (`stage_1b_hierarchy_mapper.py`) — verify no LLM usage (expected: deterministic)

- [ ] **Step 2: Estimate context window requirements for single-pass**

Read `taxonomy_reference.md` and `src/unified_pipeline/config/field_schemas_v1.1.json`. Estimate total tokens if you were to include: full CV text (~15-20K tokens for a 20-page CV) + full taxonomy (~5-8K tokens) + field schemas (~3-5K tokens) + instructions (~2-3K tokens). Total: ~25-36K tokens input. This fits comfortably in 128K+ context windows.

- [ ] **Step 3: Identify error cascading paths**

Map which stages consume the output of which other stages. Identify the longest dependency chain. Note where a single misclassification in stage 3b would propagate through 4 → 4.5 → 5c → 5d → 6.

- [ ] **Step 4: Assess consolidation opportunities**

Based on the verified stage categorization and token estimates, document which stages could be merged. Use the empirical LLM/API/deterministic findings from Step 1 — do not assume a fixed grouping. The goal is to identify the minimum number of stages that preserves quality while eliminating error cascading. Consider that stages 5b and 6 may have LLM components that affect grouping decisions.

- [ ] **Step 5: Write architecture analysis notes**

Compile findings with the consolidation proposal, token estimates, and error cascading map.

---

### Task 4: Review Existing Testing and Evaluation Infrastructure

**Purpose:** Understand what testing exists today and what gaps remain. Feeds Section 6 (Gold Standard & Regression Methodology).

**Files to read:**
- `EVALUATION_GUIDE.md` — existing evaluation methodology
- `src/unified_pipeline/test_pipeline_stages.py` — what the current tests cover
- `src/unified_pipeline/test_async_pipeline.py` — async test coverage
- `src/unified_pipeline/core/test_hierarchical_mapping.py` — core test
- `DEBUGGING_WORKFLOW.md` — debugging approach
- `src/unified_pipeline/extraction_failure_detector.py` — quality detection logic

- [ ] **Step 1: Read EVALUATION_GUIDE.md**

Summarize the evaluation methodology: phases, defect classification, fix patterns. Note what it covers and what it doesn't (e.g., no automated regression, no gold standard comparison).

- [ ] **Step 2: Read existing test files**

Read the test files. Assess: are these unit tests, integration tests, or end-to-end? Do they test against expected outputs or just check that stages run without errors? Do they use real CVs or synthetic data?

- [ ] **Step 3: Read extraction_failure_detector.py**

Understand the quality detection logic. What does it flag as a failure? Could this be adapted into a regression testing framework?

- [ ] **Step 4: Write testing gap analysis notes**

Document: what exists, what's missing, and what's needed for a gold standard + regression approach. Key gap: no automated comparison of pipeline output against expected results.

---

### Task 5: Write the Internal Technical Assessment

**Purpose:** Author the complete technical assessment document using the research gathered in Tasks 1-4.

**Files:**
- Create: `/Users/paulalbert/Dropbox/Projects/CViche - Planning/technical-assessment.md`

- [ ] **Step 1: Write Section 1 — Architecture Critique**

Using Task 3 notes. Cover:
- Current 12-stage pipeline with LLM/deterministic categorization table
- Original rationale (context window constraints)
- Changed landscape (128K-1M context windows; a 20-page CV + taxonomy + schemas fits in ~25-36K tokens)
- Consequences: 12 failure points, error cascading (map the dependency chain), whack-a-mole debugging
- Existing web interface (React/FastAPI, functional, deployed at localhost:3000/8000)

- [ ] **Step 2: Write Section 2 — Alternative Architecture Analysis**

Using Task 3 notes. For each of three approaches (consolidated, single-pass, hybrid):
- Description of what changes
- Rough LOE to prototype
- Token cost model (estimate tokens per CV)
- Risk profile (including "lost in the middle" for single-pass)
- Expected quality trade-off
- Recommendation with rationale

- [ ] **Step 3: Write Section 3 — Vendor & Model Assessment**

Using Task 1 notes. Cover:
- gpt-5.1 is the actual model (with file:line citations)
- config.py stale defaults (file:line citations)
- gpt-5.1 pricing from stage_4_field_extractor.py MODEL_PRICING
- 20+ files with direct `from openai import OpenAI` — no abstraction layer
- Claude pricing comparison (verify against current Anthropic rates; use generic "mid-tier"/"lightweight" language with note to check)
- Vendor risk: single-vendor dependency, OpenAI longevity concern
- Recommendation: Claude prototype for comparison

- [ ] **Step 4: Write Section 4 — Weakness Inventory**

Using Tasks 1-4 notes. Cover each weakness with specific codebase evidence:
- Mega-block failures (Stage 2)
- Error cascading (dependency chain map)
- No regression safety net (testing gap analysis from Task 4)
- PDF limitations
- Cost unpredictability (stale pricing in config.py means costs are undercounted)
- Prompt fragility (model-specific tuning, no prompt versioning)

- [ ] **Step 5: Write Section 5 — Data Handling & PII Considerations**

Using Task 2 notes. Cover:
- Full data flow diagram (upload → API → storage → output)
- What PII is transmitted to OpenAI (CV text including names, addresses, employment history)
- Local storage of intermediate outputs (JSON with personal data)
- Prompt logging (if enabled, full prompts/responses on disk)
- No encryption at rest, no deletion policy
- BAA/DPA investigation needed
- Whether private-endpoint or on-prem LLM options should be evaluated

- [ ] **Step 6: Write Section 6 — Gold Standard & Regression Methodology**

Using Task 4 notes. Cover:
- Prerequisite problem (can't measure anything without ground truth)
- Assisted creation approach (pipeline output + human correction)
- Time estimate caveat (untested — recommend 2-CV trial)
- Scope: what gets verified at each stage
- Automated comparison tooling design (diff pipeline output vs. gold standard)
- Regression gates (run gold standard corpus before accepting changes)
- Evaluation rubric (build on EVALUATION_GUIDE.md)
- Natural accumulation via web interface review workflow

- [ ] **Step 7: Write Section 7 — Claude Comparison Prototype Scope**

Cover:
- Corpus: same 10-15 gold standard CVs
- Target: 3-4 stage pipeline using Claude API
- Measurements: accuracy vs. gold standard, cost, speed, failure rate
- Specific test for extraction completeness ("lost in the middle")
- Deliverable: data-driven comparison report
- Dependency: gold standard must exist first

- [ ] **Step 8: Write Section 8 — Recommendations**

Prioritized table with phases, actions, dependencies, and rough LOE. Include the phased timeline from the spec (Phases 1-8).

- [ ] **Step 9: Review the complete document**

Read the entire technical assessment end-to-end. Verify:
- Every factual claim has a file:line citation or is marked as an estimate
- Tone is candid, not defensive
- Sections flow logically
- No marketing language
- Recommendations are actionable

---

### Task 6: Write the CIO One-Pager

**Purpose:** Author the CIO proposal informed by the technical assessment findings.

**Files:**
- Create: `/Users/paulalbert/Dropbox/Projects/CViche - Planning/cviche-cio-proposal.md`
- Reference: `/Users/paulalbert/Dropbox/Projects/CViche - Planning/technical-assessment.md` (just completed)

- [ ] **Step 1: Write Problem Statement**

2-3 sentences. Faculty CVs arrive in inconsistent formats. Converting them to the WCM standard template is manual, ~4 hours per CV. Bottleneck for faculty affairs during promotions and reviews. No jargon.

- [ ] **Step 2: Write "What CViche Does"**

Plain-language. Takes Word CV → AI parses structure → classifies against WCM taxonomy → enriches with PubMed → produces formatted template. Requires human review. Accelerator, not replacement.

- [ ] **Step 3: Write Operational Ownership**

- Primary users: Faculty Affairs staff and department coordinators
- Access: SAML/SSO, per-user tracking and accountability
- Operational owner: Faculty Affairs, IT for infrastructure

- [ ] **Step 4: Write Current Status & Reliability**

- Run on ~100 CVs; detailed review on smaller subset
- 4-hour task → 20 minutes (including review)
- New edge cases still surfacing — active improvement
- Formal validation and regression testing being developed
- Technical approach critically assessed internally

- [ ] **Step 5: Write "What's Needed to Deploy"**

- Infrastructure: EKS container alongside ReCiter; web interface already exists
- Auth: SAML/SSO integration
- Controls: usage limits, cost tracking, audit logging
- API costs: ~$0.10-0.30/CV (being measured precisely; model selection under evaluation)
- Staff time: 2-4 weeks engineering for initial deployment

- [ ] **Step 6: Write Risk Considerations**

- PII: CV text sent to commercial LLM API; access controls, data policies, provider evaluation required
- Cost: per-user caps and monitoring
- Vendor: single-provider dependency; alternatives under evaluation
- Accuracy: accelerator, not replacement; all output requires human verification

- [ ] **Step 7: Write Next Steps**

- Complete technical assessment and architecture evaluation
- Build validation test suite
- Controlled pilot with faculty affairs (limited users, monitored)
- Gather feedback, iterate, expand

- [ ] **Step 8: Review the CIO document**

Read end-to-end. Verify:
- Fits on one page (approximately 400-500 words)
- No marketing language, no inflated claims
- Factual, operational tone
- No technical jargon (no "gpt-5.1", no "12-stage pipeline", no "taxonomy codes")
- Risk section is honest
- Cost numbers are defensible (or flagged as estimates)
- Consistent with technical assessment findings

---

### Task 7: Final Cross-Document Review

**Purpose:** Ensure both documents tell a coherent story and the CIO doc properly abstracts the technical assessment.

**Files:**
- Review: `/Users/paulalbert/Dropbox/Projects/CViche - Planning/technical-assessment.md`
- Review: `/Users/paulalbert/Dropbox/Projects/CViche - Planning/cviche-cio-proposal.md`

- [ ] **Step 1: Check consistency**

Verify that every claim in the CIO doc is supported by the technical assessment. Check that cost numbers, timeline estimates, and risk statements align.

- [ ] **Step 2: Check abstraction level**

The CIO doc should not leak technical details. Scan for any references to specific models, stage numbers, file paths, or implementation details that don't belong in an executive document.

- [ ] **Step 3: Check word count**

CIO doc should be approximately one page (~400-500 words). If over, trim. If under, it's fine — concise is better.

- [ ] **Step 4: Commit both documents**

```bash
cd "/Users/paulalbert/Dropbox/Projects/CViche - Planning"
# Note: This is in Projects/, not a git repo. No commit needed.
# The documents are the deliverables.
```

Notify the user that both documents are ready for review.
