# CViche Proposal & Technical Assessment — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Author:** Paul Albert

## Context

The CIO has requested a one-page proposal for deploying CViche at Weill Cornell Medicine. A senior dean has already seen the tool and blessed the initiative. The goal is not to market the tool but to provide a factual operational brief covering value, institutional risk, and reliability.

Separately, a candid internal technical assessment is needed to critically evaluate the current architecture, identify weaknesses, assess alternative approaches (including a potential fundamental rethink), and define a testing/regression methodology. The assessment informs the proposal — findings from the assessment shape what the CIO document commits to.

## Deliverables

### Document 1: Internal Technical Assessment

**Audience:** Paul Albert, technical team
**Tone:** Fully candid
**Location:** `/Users/paulalbert/Dropbox/Projects/CViche - Planning/technical-assessment.md`

#### Sections

**1. Architecture Critique**
- Current state: 12-stage LLM/deterministic pipeline (stages 1a through 6)
- Original design rationale: context window constraints forced decomposition — couldn't feed a full CV + taxonomy + extraction instructions in a single LLM pass
- What's changed: GPT-4o has 128K context, Claude has 200K, GPT-4.1 has 1M. A 20-page CV is ~15-20K tokens. The original constraint may no longer justify the complexity.
- Consequence: 12 stages means 12 points of failure, error cascading between stages, and a whack-a-mole bug pattern where fixing one stage breaks downstream behavior
- Current web interface: A functioning React/FastAPI web interface already exists (`web_interface/`) with file upload, real-time pipeline visualization, cost tracking, and output downloads. Deployment planning should build on this, not start from scratch.

**2. Alternative Architecture Analysis**
Evaluate three approaches with trade-offs:
- **Consolidated pipeline:** Collapse stages 1a-3b into a single "segment + classify" pass; collapse 4-5d into a single "extract + enrich + format" pass. Reduces to ~4 stages.
- **Single-pass extraction:** One LLM call with full CV + taxonomy + field schemas in context. LLM returns structured JSON. Followed by deterministic enrichment (PubMed, ROR) and template generation. Reduces to ~3 stages. **Note:** Single-pass extraction over long documents is a known challenge for current LLMs — models can lose items from the middle of long inputs ("lost in the middle" effect). The assessment should design a test that specifically measures extraction completeness across document length, not just assume quality parity.
- **Hybrid:** Keep stages that genuinely benefit from separation (e.g., PubMed enrichment is API-based, template generation is deterministic), merge the LLM-dependent stages.

**Evaluation criteria for each approach:**
- Rough level of effort to prototype (days/weeks)
- Token cost modeling: estimated tokens per CV under each architecture
- Risk profile: what could go wrong, how recoverable
- Expected quality vs. current pipeline (measured against gold standard once available)
- Whether a recommendation emerges, or whether the section is purely analytical (recommendation preferred if data supports it)

**3. Vendor & Model Assessment**
- Current state: Pipeline uses OpenAI API exclusively — gpt-5.1 is the default model across all stages (`run_full_pipeline.py` defaults to gpt-5.1). The `extraction_failure_detector.py` describes a gpt-4o-mini-first/gpt-5.1-retry pattern, but this is not wired into the main pipeline. In practice, all stages use gpt-5.1.
- Cost reality: Using gpt-5.1 across all stages means actual per-CV cost is at the higher end. The config.py pricing table only covers gpt-4o-mini and gpt-4o — it does not reflect the model actually in use.
- Claude comparison: Anthropic's mid-tier model (Sonnet) is in a comparable price range to gpt-5.1; their lightweight model (Haiku) could serve as a cheaper tier for simpler stages. Exact pricing should be verified against current Anthropic rate cards at time of writing.
- Vendor risk: OpenAI's long-term viability is uncertain; single-vendor dependency is an institutional risk
- Current codebase is NOT model-agnostic — uses OpenAI Python SDK directly; switching requires API client swap and prompt re-tuning
- Recommendation: Prototype a Claude-based pipeline to compare quality, cost, and architectural simplification potential
- **Action item:** Measure actual per-CV API cost by running 3 representative CVs through the current pipeline and capturing token usage/spend. This replaces guesswork with data.

**4. Weakness Inventory**
Honest catalog of known problems:
- **Mega-block failures:** Stage 2 sometimes merges 20+ items from table columns into a single entry; downstream stages only extract the first item (~12% coverage)
- **Error cascading:** A misclassification in Stage 3b propagates through field extraction, formatting, and template generation — each stage amplifies upstream errors
- **Whack-a-mole bugs:** No regression safety net; fixing one CV's output can break another's. Incremental fixes accumulate without systematic verification.
- **PDF limitations:** Vision-based parsing is higher cost and lower quality than Word processing
- **Cost unpredictability:** Edge cases (very long CVs, unusual formats) can produce unexpectedly high API costs
- **Prompt fragility:** LLM prompts are tuned empirically; small model updates can shift behavior unpredictably

**5. Data Handling & PII Considerations**
- CV text is sent to external commercial LLM APIs (currently OpenAI) for processing — faculty PII leaves the institution's network
- Intermediate pipeline outputs (JSON files with extracted personal data) are stored locally
- No formal data retention or deletion policy for processed CVs
- The technical assessment should document the full data flow: upload → API transmission → intermediate storage → output — so that IT security questions can be answered concretely
- Evaluate whether on-premise or private-endpoint LLM options are warranted

**6. Gold Standard & Regression Methodology**
- **The prerequisite problem:** Cannot compare architectures, measure regression, or evaluate Claude vs. GPT without reliable ground truth
- **Assisted gold standard creation:** Run pipeline on 10-15 representative CVs, then present output in a structured review interface where a knowledgeable reviewer corrects what's wrong (validating, not creating from scratch). Time estimate of ~30-40 minutes per CV is untested — recommend a time-boxed trial on 2 CVs to validate the estimate before committing to the full set.
- **Gold standard scope:** Full pipeline output — segmentation, classification, field extraction, final template — verified at each stage
- **Automated comparison tooling:** Diff tool that compares pipeline output against gold standard, reports per-stage accuracy, flags regressions
- **Regression gates:** Automated checks that run against gold standard corpus before any pipeline change is accepted
- **Evaluation rubric:** Formalized scoring (building on existing EVALUATION_GUIDE.md) with per-section weights
- **Natural accumulation:** Review/correction workflow in the web interface could build gold standard as a byproduct of normal usage (connects to future UI/UX audit)

**7. Claude Comparison Prototype Scope**
Concrete plan for a head-to-head evaluation:
- **Corpus:** Same 10-15 gold standard CVs
- **Test pipeline:** Simplified Claude-based pipeline (target: 3-4 stages vs. current 12)
- **Measurements:** Per-stage accuracy vs. gold standard, total cost per CV, processing time, failure rate
- **Design:** Feed full CV + taxonomy + extraction schema in a single context window; evaluate whether quality matches or exceeds the multi-stage approach. Specifically test extraction completeness across full document length to assess "lost in the middle" risk.
- **Deliverable:** Comparison report with data, not opinions
- **Dependency:** Requires gold standard to exist first

**8. Recommendations**
Prioritized action items with dependencies:

| Phase | Action | Depends on | Rough LOE |
|-------|--------|------------|-----------|
| 1 | Measure actual per-CV cost (3 representative CVs) | — | 1 day |
| 2 | Build assisted gold standard (10-15 CVs) | — | 1-2 weeks |
| 3 | Establish automated regression testing | Phase 2 | 1 week |
| 4 | Prototype consolidated/single-pass pipeline with Claude | Phase 2 | 2-3 weeks |
| 5 | Run head-to-head comparison (GPT vs. Claude) | Phases 2-4 | 1 week |
| 6 | Decision: harden current pipeline or migrate | Phase 5 | — |
| 7 | Deploy to EKS with auth, usage controls, monitoring | Phase 6 | 2-4 weeks |
| 8 | Pilot with faculty affairs staff, iterate | Phase 7 | Ongoing |

Timeline estimates are rough and will be refined during implementation planning.

---

### Document 2: CIO One-Pager

**Audience:** CIO, potentially passed to IT leadership and other stakeholders
**Tone:** Factual, operational, no marketing. The "why" is already settled.
**Location:** `/Users/paulalbert/Dropbox/Projects/CViche - Planning/cviche-cio-proposal.md`

#### Sections

**1. Problem Statement**
2-3 sentences. Faculty CVs arrive in inconsistent formats. Converting them to the WCM standard template is a manual process that takes approximately 4 hours per CV with review. This creates a bottleneck for faculty affairs during promotions and annual reviews.

**2. What CViche Does**
Plain-language description. Takes a faculty CV in Word format, uses AI to parse the structure, classify entries against WCM's 60-code taxonomy, enrich publications with PubMed metadata, and produce a formatted WCM template document. Output requires human review — the tool accelerates the process, it does not replace judgment.

**3. Operational Ownership**
- **Primary users (near-term):** Faculty Affairs staff and department coordinators processing CVs on behalf of faculty for promotions and annual reviews
- **Access model:** Authenticated users only (SAML/SSO), with per-user usage tracking and accountability
- **Operational owner:** Faculty Affairs, with infrastructure support from IT

**4. Current Status & Reliability**
- Pipeline has been run on approximately 100 faculty CVs; detailed quality review has been performed on a smaller subset. New edge cases still surface as the corpus grows.
- Reduces a 4-hour manual task to approximately 20 minutes (including human review)
- Active improvement ongoing — a formal validation test suite and regression testing methodology are being developed
- Technical approach has been critically assessed internally; architecture refinements are scoped

**5. What's Needed to Deploy**
- **Infrastructure:** Containerized deployment on EKS (alongside existing ReCiter stack). A functioning web interface already exists and would be deployed as part of the application.
- **Authentication:** SAML/SSO integration with WCM identity systems, per-user access control
- **Operational controls:** Per-user usage limits, cost tracking per department, audit logging
- **API costs:** Commercial LLM API — cost per CV is currently being measured precisely; early estimates are in the range of $0.10-0.30 per CV. Model selection is under active evaluation to optimize cost and quality.
- **Staff time:** Estimated 2-4 weeks of engineering effort for initial deployment (containerization, auth integration, monitoring). Additional ongoing time for user support and pipeline refinement.

**6. Risk Considerations**
- **Data sensitivity:** CVs contain personally identifiable information. CV text is sent to a commercial LLM API for processing. Appropriate access controls, audit logging, data handling policies, and API provider evaluation are required.
- **Cost control:** Per-user usage caps and monitoring prevent runaway API spend
- **Vendor dependency:** Currently relies on a single commercial LLM provider; evaluating alternative providers to reduce single-vendor risk
- **Accuracy:** The tool is an accelerator, not a replacement for human review. All output requires verification before institutional use.

**7. Next Steps**
- Complete internal technical assessment and architecture evaluation
- Build validation test suite for quality assurance and regression testing
- Controlled pilot with faculty affairs staff (limited user group, monitored usage)
- Gather feedback, iterate, expand access

---

### Future: UI/UX Audit

Deferred until both documents are finalized and the technical approach is settled.

## Design Decisions & Rationale

| Decision | Rationale |
|----------|-----------|
| Assess-first, then propose | CIO is averse to marketing; a proposal backed by real analysis carries more weight |
| Two separate documents | CIO doesn't want technical minutiae; technical team needs candid assessment |
| Claude prototype as formal recommendation | Vendor diversification concern, political aversion to OpenAI, comparable pricing at gpt-5.1 tier, hypothesis that larger context could simplify architecture |
| Gold standard before architecture comparison | Can't measure improvement without ground truth |
| Assisted (not manual) gold standard creation | Time constraint — reviewer corrects pipeline output rather than creating from scratch |
| CIO doc is model/vendor agnostic | Says "commercial LLM API" not "OpenAI" — gives flexibility as vendor evaluation proceeds |
| UI/UX audit deferred | Depends on settling the technical approach and deliverable scope first |
| PII/data flow in technical assessment | CIO doc raises PII risk; technical assessment must have the concrete answer ready for IT security |

## Open Questions

1. Which 10-15 CVs should form the gold standard? Need representative diversity (junior/senior, different departments, varying CV lengths and formats).
2. Who reviews the gold standard CVs? Paul alone, or can a faculty affairs staff member assist?
3. Timeline expectations — when does the CIO need the proposal?
