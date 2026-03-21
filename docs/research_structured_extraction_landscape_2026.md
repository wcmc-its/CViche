# Structured Document Extraction Landscape: March 2026

## Research Report for CViche Pipeline Modernization

**Date:** 2026-03-19
**Context:** Evaluating whether the 12-stage CViche pipeline (9 LLM calls via GPT-5.1) can be replaced or dramatically simplified using current tools and platforms.

---

## Executive Summary

The document extraction landscape has shifted significantly since the CViche pipeline was designed. Three developments are most relevant:

1. **Structured Outputs are now native** across all major LLM providers (OpenAI, Anthropic, Google) with guaranteed JSON schema conformance. This eliminates the need for custom parsing/validation of LLM responses.

2. **Purpose-built extraction platforms** (Reducto, LlamaExtract, LandingAI) have matured to the point where they can handle custom schemas with citation-level provenance -- but none handle custom taxonomies out of the box.

3. **The Instructor library** has emerged as the production standard for multi-provider structured extraction with Pydantic models, supporting 15+ providers through a unified `from_provider()` API.

**Bottom line:** A full replacement with a single commercial platform is not viable because no platform supports CViche's custom taxonomy (70+ categories with hierarchical relationships). However, the pipeline can likely be collapsed from 12 stages / 9 LLM calls to approximately 3-4 stages / 3-4 LLM calls using Instructor + native structured outputs + a modern document parser, cutting cost by 40-60%.

---

## 1. Purpose-Built Document Extraction Platforms

### 1.1 Reducto (reducto.ai)
- **Status:** Production-ready. $108M total funding through Feb 2026 (Series B).
- **What it does:** Vision-first, multi-pass document intelligence. Parse, Extract, Split, and Edit endpoints. Returns structure-preserving JSON with bounding-box provenance.
- **Custom schema support:** Yes -- you define a JSON schema and it extracts matching fields with confidence scores and source citations.
- **Academic CV handling:** Could handle parsing and layout extraction, but does NOT support custom taxonomy classification. It extracts fields you define, not categorize content into your taxonomy.
- **Pricing:** Starts at ~$0.015/page for parsing. Agentic OCR roughly doubles credit cost. Extract endpoint costs additional credits.
- **For 1,000 CVs (avg 25 pages = 25,000 pages):** ~$375-$750 for parsing; extraction would add more.
- **Verdict:** Strong for parsing PDF/DOCX to structured text. Not a replacement for taxonomy classification stages.
- **Sources:** [Reducto Pricing](https://reducto.ai/pricing), [Reducto vs LlamaParse](https://llms.reducto.ai/reducto-vs-llamaparse)

### 1.2 LlamaParse / LlamaExtract (LlamaIndex)
- **Status:** Production-ready. Credit-based pricing.
- **LlamaParse:** Converts documents to text/Markdown/JSON. Multiple quality tiers from 1 credit/page (no AI) to 90 credits/page (Sonnet 4.0 agent).
- **LlamaExtract:** Schema-based extraction with confidence scores and citations. Define a Pydantic-style JSON schema, get structured data back. Fast mode ~5 credits/page, Premium ~60 credits/page.
- **Pricing:** 1,000 credits = $1.25. For 25,000 pages at Premium extraction: ~60 credits x 25,000 = 1.5M credits = **~$1,875**.
- **Custom taxonomy:** LlamaExtract supports custom schemas but not hierarchical taxonomy classification. You'd need to define extraction schemas per taxonomy category.
- **Verdict:** Good value for document parsing. LlamaExtract could potentially replace Stage 4 (field extraction) if schemas are pre-mapped to taxonomy codes.
- **Sources:** [LlamaParse Pricing](https://www.llamaindex.ai/pricing), [LlamaExtract Schema Design](https://docs.cloud.llamaindex.ai/llamaextract/features/schema_design)

### 1.3 Unstructured.io
- **Status:** Production-ready. Open-source core + commercial SaaS platform.
- **What it does:** ETL pipeline for 65+ file types. Partitioning, enrichment, chunking, embedding. Delivers to 30+ destinations.
- **Key strength:** Open-source library can be self-hosted. Best suited as a preprocessing layer (PDF-to-text/chunks) rather than structured extraction.
- **Custom taxonomy:** No built-in taxonomy support. It provides document elements (titles, text blocks, tables) but doesn't classify into custom categories.
- **Pricing:** Free tier (15,000 pages). Pay-as-you-go pricing (specific rate not publicly listed; requires contact).
- **Verdict:** Good replacement for Stage 1a (segmentation) preprocessing, but doesn't address classification or extraction stages.
- **Sources:** [Unstructured Pricing](https://unstructured.io/pricing), [Unstructured GitHub](https://github.com/Unstructured-IO/unstructured)

### 1.4 Docugami
- **Status:** Production, enterprise-focused. Expanded to Europe (June 2025).
- **What it does:** Full document understanding with Knowledge Graph-RAG. Creates complete data representation including clauses, tables, relationships. Uses smaller efficient models that can outperform GPT-4 on specific tasks.
- **Document types:** Contracts, policies, proposals, regulatory docs. NOT designed for CVs/resumes.
- **Custom taxonomy:** No -- it auto-discovers document structure rather than mapping to user-defined taxonomies.
- **Verdict:** Wrong fit. Designed for long-form business documents, not CV parsing.
- **Sources:** [Docugami](https://www.docugami.com/), [Docugami How It Works](https://www.docugami.com/how-it-works)

### 1.5 Docling (IBM, Open Source)
- **Status:** Production-ready. MIT license. 37,000+ GitHub stars. 100+ releases since Aug 2025.
- **What it does:** Open-source document conversion toolkit. PDF, DOCX, PPTX, XLSX, HTML, audio, video. TableFormer model for complex tables. Heron layout model (Dec 2025) for fast PDF parsing.
- **Granite-Docling-258M:** Compact vision-language model (Apache 2.0) for end-to-end document conversion released early 2026.
- **Custom taxonomy:** No taxonomy support. Produces structured document representation (text blocks, tables, figures) but doesn't classify content.
- **Pricing:** Free (MIT license). Self-hosted.
- **Verdict:** Excellent free alternative to LlamaParse/Reducto for the parsing stage. Could replace Stage 1a preprocessing. Distributed processing via Ray Data for scale.
- **Sources:** [Docling GitHub](https://github.com/docling-project/docling), [Granite-Docling](https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion)

### 1.6 LandingAI (Agentic Document Extraction)
- **Status:** Production. DPT-2 model released Sept 2025. 99.16% DocVQA accuracy.
- **What it does:** Vision-first extraction preserving layout and spatial context. Extracts text, tables, charts, form fields with bounding box coordinates.
- **Key innovation:** "Agentic" approach where the system iteratively refines extraction, identifies checkboxes, flowcharts, financial tables. Up to 90% reduction in search time.
- **Custom taxonomy:** No. Extracts document elements but doesn't classify into custom categories.
- **Verdict:** Overkill for CV parsing. Best suited for complex visual documents (forms, engineering drawings, financial statements).
- **Sources:** [LandingAI ADE](https://landing.ai/agentic-document-extraction), [DeepLearning.AI Course](https://www.deeplearning.ai/short-courses/document-ai-from-ocr-to-agentic-doc-extraction/)

### 1.7 Cloud Provider Solutions

| Platform | Custom Extraction | Pricing (per 1K pages) | Academic CV Fit |
|----------|------------------|----------------------|-----------------|
| Google Document AI Custom Extractor | Yes (10-50 training examples) | $30 | Medium -- requires training data |
| Azure Document Intelligence (Custom Generative) | Yes | $30 | Medium -- same limitation |
| Azure Document Intelligence (Custom Standard) | Yes | $3 | Low -- needs many training examples |
| Amazon Textract Queries | Yes (natural language) | $15 | Medium -- limited to 15-30 queries/page |

**Key limitation for all cloud providers:** They require training examples or predefined queries. None can dynamically apply a 70+ category hierarchical taxonomy like CViche uses. You'd need to pre-define queries/fields for each taxonomy category, which defeats the purpose of the taxonomy-driven approach.

**Sources:** [Google Document AI Pricing](https://cloud.google.com/document-ai/pricing), [Azure Document Intelligence Pricing](https://azure.microsoft.com/en-us/pricing/details/document-intelligence/), [Amazon Textract Pricing](https://aws.amazon.com/textract/pricing/)

---

## 2. LLM-Native Structured Extraction Libraries

### 2.1 Instructor (RECOMMENDED)
- **Status:** Production-ready. 1M+ monthly downloads. Latest release: Jan 29, 2026.
- **What it does:** Wraps any LLM provider to guarantee Pydantic-validated structured output. Handles validation, retries, streaming.
- **Provider support:** 15+ providers via `from_provider()` -- OpenAI, Anthropic, Google, Ollama, DeepSeek, Mistral, Cohere, etc. Switch providers by changing `model_name`.
- **Key features:**
  - Define output schemas as Pydantic models
  - Automatic retry on validation failure
  - Streaming support for partial outputs
  - Semantic validation (2025 addition)
  - Works with OpenAI Responses API (2025 addition)
- **Why this matters for CViche:** You could define each pipeline stage's output as a Pydantic model and use Instructor to call any provider with guaranteed schema conformance. This eliminates custom JSON parsing/validation code.
- **Example for taxonomy mapping:**
  ```python
  class TaxonomyMapping(BaseModel):
      header_text: str
      taxonomy_code: str
      confidence: float
      reasoning: str
  ```
- **Verdict:** The single most impactful library for simplifying CViche. Reduces validation/retry/parsing boilerplate dramatically.
- **Sources:** [Instructor Docs](https://python.useinstructor.com/), [Instructor GitHub](https://github.com/567-labs/instructor), [Instructor PyPI](https://pypi.org/project/instructor/)

### 2.2 Marvin (Prefect)
- **Status:** Active. Marvin 3.0 uses PydanticAI under the hood.
- **What it does:** High-level AI tasks: classify, extract, cast, generate, summarize. Simple one-liner API.
- **Limitations:** Historically OpenAI-only, now expanded via PydanticAI. Less control than Instructor for complex extraction pipelines.
- **Verdict:** Too high-level for CViche's needs. Instructor offers better control.
- **Sources:** [Marvin GitHub](https://github.com/PrefectHQ/marvin), [Marvin Docs](https://askmarvin.ai/welcome)

### 2.3 PydanticAI
- **Status:** Active, growing. From the Pydantic team.
- **What it does:** Agent framework with schema-first extraction and tool use. Provider-agnostic (OpenAI, Claude, Gemini).
- **Relation to Instructor:** Complementary. PydanticAI is more of an agent framework; Instructor is more of a structured output wrapper.
- **Verdict:** Worth watching but Instructor is more mature for pure extraction use cases.
- **Sources:** [PydanticAI PyPI](https://pypi.org/project/pydantic-ai/)

### 2.4 Outlines / Guidance (Constrained Generation)
- **Status:** Open-source. For local/self-hosted models.
- **What they do:** Constrained token sampling that guarantees valid output by manipulating the model's logits during generation. More efficient than function calling for local models.
- **Limitation:** Only works with models you host yourself. Not applicable to API-based models (OpenAI, Anthropic).
- **Verdict:** Not relevant unless you switch to self-hosted models.
- **Sources:** [Guidance GitHub](https://github.com/guidance-ai/llguidance)

### 2.5 LangChain Extraction
- **Status:** Production. `with_structured_output()` method available on all ChatModels.
- **What it does:** Extraction chains with schema definitions, prompts, and reference examples. Open-source extraction service available.
- **Verdict:** Viable but adds significant LangChain dependency overhead. Instructor achieves the same result with less abstraction.
- **Sources:** [LangChain Extraction](https://python.langchain.com/docs/use_cases/extraction/)

### 2.6 Unstract
- **Status:** Open-source (GitHub). Production-ready with Prompt Studio for no-code extraction.
- **What it does:** LLM-powered ETL for unstructured documents. Define extraction prompts in natural language, deploy as API or ETL pipeline. Uses "LLMChallenge" (two LLMs extract + verify).
- **AI-stack agnostic:** Works with any LLM (DeepSeek, Mistral, Llama), any vector DB, any embedding model.
- **Verdict:** Interesting for a full pipeline replacement but requires significant reconfiguration. More suited for invoice/form extraction than academic CV taxonomy mapping.
- **Sources:** [Unstract GitHub](https://github.com/Zipstack/unstract), [Unstract Docs](https://docs.unstract.com/)

---

## 3. Provider-Specific Capabilities

### 3.1 OpenAI (GPT-5.x + Responses API)
- **Structured Outputs:** Native support with `json_schema` response format. Guaranteed schema conformance.
- **Responses API:** Replaces deprecated Assistants API (shutdown Aug 26, 2026). Includes file search with vector stores (up to 100M files).
- **Batch API:** 50% discount on all token costs. 24-hour turnaround. Perfect for CV processing.
- **Prompt Caching:** 90% discount on repeated prompts (GPT-5 family). Huge for CViche since the same system prompt + taxonomy is sent repeatedly.
- **Current pricing (GPT-5.1):**
  - Standard: Not explicitly listed in search results; GPT-5.2 is $1.75/$14.00 per 1M tokens
  - Batch: 50% off standard
  - With prompt caching: Additional 90% off cached input tokens
- **Verdict:** CViche already uses GPT-5.1. Adding batch API + prompt caching could cut costs 60-80% without changing the pipeline at all.
- **Sources:** [OpenAI Pricing](https://openai.com/api/pricing/), [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)

### 3.2 Anthropic Claude
- **Structured Outputs:** GA on Claude Sonnet 4.5, Opus 4.5, Haiku 4.5 (Haiku 4.5 added Feb 2026). Guaranteed JSON schema conformance.
- **Files API:** Beta since April 2025. Upload once, reference by file_id. Up to 30MB per file, 20 files per chat.
- **PDF Support:** Analyzes text and visual elements in PDFs under 100 pages. Can extract structured JSON.
- **Pricing:** Sonnet 4.6: $3/$15; Haiku: $0.25/$1.25 per 1M tokens. Caching offers 90% discount on reused context.
- **Verdict:** Strong alternative to GPT-5.1 for extraction. Haiku at $0.25/$1.25 could be cost-effective for simpler stages.
- **Sources:** [Claude Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Claude PDF Support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)

### 3.3 Google Gemini
- **Structured Outputs:** Native support via `response_schema` parameter. JSON schema conformance.
- **1M+ context window:** All Gemini models support 1M tokens. Could process entire 40-page CV in a single call.
- **Key advantage:** Native PDF text tokens are FREE -- you're not charged for tokens from extracted native text in PDFs.
- **Pricing:**
  - Gemini 2.5 Pro: $1.25/$10.00 per 1M tokens
  - Gemini 2.5 Flash: $0.30/$2.50 per 1M tokens
  - Flash-Lite: $0.10/$0.40 per 1M tokens
  - Batch API: 50% off all models
- **Verdict:** Gemini Flash + Batch API could be the cheapest option for CV extraction. Free PDF text tokens is a major cost advantage. The 1M context window means an entire CV can be processed in one call without chunking.
- **Sources:** [Gemini Pricing](https://ai.google.dev/gemini-api/docs/pricing), [Gemini Document Processing](https://ai.google.dev/gemini-api/docs/document-processing), [Gemini Structured Output](https://ai.google.dev/gemini-api/docs/structured-output)

### 3.4 Google NotebookLM
- **Status:** Enterprise version has API (notebooks management, source management).
- **What it can do:** Upload documents, ask questions, get synthesized answers. Automated RAG pipeline.
- **Limitations:** Designed for knowledge synthesis, not structured extraction. No schema-based extraction API. No custom taxonomy support.
- **Verdict:** Not suitable for CV parsing pipeline. It's a research/analysis tool, not an extraction API.
- **Sources:** [NotebookLM Enterprise API](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks)

---

## 4. Open-Source CV/Resume Parsers

### 4.1 Current State (March 2026)

No open-source CV parser handles academic CVs well. Here's why:

| Parser | Approach | Academic CV Support | Notes |
|--------|----------|-------------------|-------|
| OpenResume | Rule-based | Poor | Designed for 1-2 page resumes, not 40-page academic CVs |
| ResuLLMe | LLM-based (OpenAI/Gemini) | Poor | Tailoring/enhancement tool, not extraction |
| OmkarPathak ResumeParser | Agentic AI (Qwen2.5) | Poor | Privacy-focused local parsing, basic fields only |
| Unstract Resume Parser | LLM-based | Medium | Generic resume parsing, not academic-specific |
| Smart-Hiring Pipeline | NLP + NER | Poor | HR-focused, not academic |

**Key gap:** Academic CVs are fundamentally different from resumes:
- 10-40 pages vs. 1-2 pages
- 70+ distinct section types (publications, grants, editorial boards, CME activities, etc.) vs. ~6 standard sections
- Custom institutional taxonomies vs. standard resume categories
- Deeply nested hierarchies (e.g., Publications > Peer-Reviewed > Original Articles vs. Publications > Book Chapters)
- No existing tool handles this complexity

**Verdict:** No off-the-shelf CV parser can replace CViche. This remains a custom problem requiring custom code.

**Sources:** [OpenResume](https://www.open-resume.com/resume-parser), [Resume Parser GitHub Topic](https://github.com/topics/resume-parser)

---

## 5. Critical Research Finding: ExtractBench (Feb 2026)

A February 2026 benchmark paper, [ExtractBench](https://arxiv.org/abs/2602.12247), tested frontier models on complex structured extraction tasks and found:

- **GPT-5/5.2, Gemini-3 Flash/Pro, Claude 4.5 Opus/Sonnet all remain unreliable on realistic schemas**
- Performance degrades sharply with schema breadth
- **0% valid output on a 369-field financial reporting schema** across ALL tested models
- GPT-5's pass rate on credit agreements fell from 86.9% to 70.0% with constrained decoding, suggesting the grammar maintenance competes with the model's content processing

**Implication for CViche:** A single-call approach that extracts all 70+ taxonomy categories into one massive JSON schema will likely fail. The current multi-stage approach (segmentation -> classification -> extraction) is architecturally sound. The optimization should focus on reducing the number of stages, not collapsing to a single call.

---

## 6. Cost Comparison for 1,000 Academic CVs

**Assumptions:** 1,000 CVs, average 25 pages each = 25,000 pages total. Average ~8,000 tokens input + ~3,000 tokens output per LLM call.

### 6.1 Current CViche Pipeline Cost

Using GPT-5.1 with 9 LLM calls per CV:
- Estimated at GPT-5.2 rates ($1.75/$14.00 per 1M tokens)
- 9 calls x 1,000 CVs = 9,000 LLM calls
- ~72M input tokens + ~27M output tokens
- **Standard: ~$504** ($126 input + $378 output)
- **With Batch API (50% off): ~$252**
- **With Batch + Prompt Caching (90% off cached input): ~$140-180**

### 6.2 Alternative Approaches

| Approach | Estimated Cost | LLM Calls | Quality | Complexity |
|----------|---------------|-----------|---------|------------|
| **Current CViche (GPT-5.1, standard)** | ~$504 | 9,000 | High (tuned) | High (12 stages) |
| **CViche + Batch API + Caching** | ~$150 | 9,000 | Same | Same |
| **Simplified CViche (3-4 stages) + Instructor** | ~$200 | 3,000-4,000 | Medium-High | Medium |
| **Gemini 2.5 Flash + Batch** | ~$60-80 | 3,000-4,000 | Medium | Medium |
| **Gemini Flash-Lite + Batch** | ~$15-25 | 3,000-4,000 | Lower | Medium |
| **Claude Haiku 4.5** | ~$35-50 | 3,000-4,000 | Medium | Medium |
| **Reducto Parse + GPT extraction** | ~$500-800 | 2,000-3,000 | High | Low-Medium |
| **LlamaExtract (Premium)** | ~$1,875 | N/A (platform) | Medium | Low |
| **Google Document AI Custom** | ~$750 | N/A (platform) | Medium | Low (after training) |
| **Azure Doc Intelligence Custom** | ~$750 | N/A (platform) | Medium | Low (after training) |
| **Docling (self-hosted) + LLM** | ~$60-150 (LLM only) | 3,000-4,000 | Medium | Medium-High |

### 6.3 Best Quality-to-Cost Ratio

**Winner: CViche pipeline + Batch API + Prompt Caching.** Adding batch processing and prompt caching to the existing pipeline cuts cost from ~$504 to ~$150 with zero quality loss and minimal code changes.

**Runner-up: Simplified pipeline (3-4 stages) using Gemini 2.5 Flash + Batch API.** ~$60-80 for 1,000 CVs with good quality, and free PDF text input tokens. However, requires rewriting pipeline stages and revalidating quality.

---

## 7. Recommended Strategy

### Phase 1: Quick Wins (No Architecture Change)
1. **Add OpenAI Batch API support.** Process CVs in 24-hour batches. Immediate 50% cost reduction.
2. **Add prompt caching.** The taxonomy prompt and system instructions are identical across CVs. Cache them for 90% input discount.
3. **Use Instructor library** to replace custom JSON parsing/validation code. Define Pydantic models for each stage's output. Automatic retry on validation failure.

### Phase 2: Pipeline Simplification (Medium Effort)
4. **Merge stages where possible:**
   - Stages 1a + 1b (segmentation + hierarchy mapping) -> single stage with structured output
   - Stages 3a + 3b (header taxonomy + entry classification) -> single stage with taxonomy context in prompt
   - Stages 5c + 5d (teaching + citation formatting) -> single formatting stage
5. **Consider Gemini 2.5 Flash** for the simpler stages (segmentation, formatting) where GPT-5.1 quality is overkill. Free PDF text input is a significant cost advantage.
6. **Use Docling (free, MIT)** as an alternative to the current PDF/DOCX text extraction in Stage 1a preprocessing.

### Phase 3: Architecture Evaluation (High Effort)
7. **Evaluate Reducto or LlamaExtract** for Stage 4 (field extraction) specifically. These platforms excel at schema-based extraction with provenance.
8. **Test Gemini 2.5 Pro with 1M context** for a "whole-CV-in-one-call" approach for simpler CVs (<15 pages). Use the current multi-stage pipeline as fallback for complex CVs.
9. **Build a quality benchmark** modeled on ExtractBench methodology to objectively compare approaches before committing to a rewrite.

### What NOT to Do
- **Do not try to replace the entire pipeline with a single platform.** No platform supports custom hierarchical taxonomies with 70+ categories.
- **Do not use a single massive JSON schema in one LLM call.** ExtractBench shows this fails at scale.
- **Do not switch to open-source CV parsers.** None handle academic CVs.
- **Do not invest in Google Document AI or Azure custom extractors** for this use case. The training data requirement and lack of taxonomy flexibility make them poor fits.

---

## 8. Tools Maturity Summary

| Tool/Platform | Maturity | CViche Relevance | Production-Ready |
|---------------|----------|-----------------|-----------------|
| Instructor | High | **Very High** -- direct replacement for JSON parsing/validation | Yes |
| OpenAI Batch API | High | **Very High** -- immediate 50% cost savings | Yes |
| OpenAI Prompt Caching | High | **Very High** -- 90% off repeated prompts | Yes |
| Gemini 2.5 Flash | High | **High** -- cheap alternative for simpler stages | Yes |
| Docling | High | **High** -- free document parsing replacement | Yes |
| Reducto | High | Medium -- strong parser, but adds cost | Yes |
| LlamaExtract | Medium-High | Medium -- schema extraction, but expensive at scale | Yes |
| Claude Structured Outputs | High | Medium -- alternative provider option | Yes |
| Unstructured.io | High | Medium -- preprocessing only | Yes |
| PydanticAI | Medium | Low-Medium -- agent framework, not extraction-focused | Yes |
| LandingAI ADE | High | Low -- overkill for CVs | Yes |
| Docugami | High | Low -- wrong document type | Yes |
| NotebookLM API | Medium | Low -- synthesis tool, not extraction | Partial |
| Open-source CV parsers | Low | None -- cannot handle academic CVs | No |

---

## Sources

### Document Extraction Platforms
- [Reducto](https://reducto.ai/)
- [Reducto Pricing](https://reducto.ai/pricing)
- [Reducto vs LlamaParse Comparison](https://llms.reducto.ai/reducto-vs-llamaparse)
- [LlamaParse / LlamaExtract](https://www.llamaindex.ai/llamaparse)
- [LlamaIndex Pricing](https://www.llamaindex.ai/pricing)
- [LlamaExtract Schema Design](https://docs.cloud.llamaindex.ai/llamaextract/features/schema_design)
- [Unstructured.io](https://unstructured.io/)
- [Unstructured GitHub](https://github.com/Unstructured-IO/unstructured)
- [Docugami](https://www.docugami.com/)
- [Docling GitHub](https://github.com/docling-project/docling)
- [IBM Granite-Docling Announcement](https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion)
- [LandingAI Agentic Document Extraction](https://landing.ai/agentic-document-extraction)
- [Unstract GitHub](https://github.com/Zipstack/unstract)
- [Document Parser Comparison (Reducto)](https://llms.reducto.ai/document-parser-comparison)

### Cloud Provider Solutions
- [Google Document AI Pricing](https://cloud.google.com/document-ai/pricing)
- [Google Document AI Custom Extractor](https://docs.cloud.google.com/document-ai/docs/ce-with-genai)
- [Azure Document Intelligence Pricing](https://azure.microsoft.com/en-us/pricing/details/document-intelligence/)
- [Amazon Textract Pricing](https://aws.amazon.com/textract/pricing/)
- [Amazon Textract Features](https://aws.amazon.com/textract/features/)

### LLM Structured Output Libraries
- [Instructor Documentation](https://python.useinstructor.com/)
- [Instructor GitHub](https://github.com/567-labs/instructor)
- [Instructor PyPI](https://pypi.org/project/instructor/)
- [Top 5 Structured Output Libraries 2026](https://dev.to/nebulagg/top-5-structured-output-libraries-for-llms-in-2026-48g0)
- [Marvin GitHub](https://github.com/PrefectHQ/marvin)
- [PydanticAI PyPI](https://pypi.org/project/pydantic-ai/)
- [LangChain Extraction](https://python.langchain.com/docs/use_cases/extraction/)
- [Structured Output Library Comparison](https://simmering.dev/blog/structured_output/)

### LLM Provider Capabilities
- [OpenAI API Pricing](https://openai.com/api/pricing/)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [OpenAI Responses API File Search](https://cookbook.openai.com/examples/file_search_responses)
- [Claude Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Claude PDF Support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)
- [Gemini Document Processing](https://ai.google.dev/gemini-api/docs/document-processing)
- [Gemini Structured Output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [LLM API Pricing Comparison 2026](https://www.tldl.io/resources/llm-api-pricing-2026)
- [NotebookLM Enterprise API](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks)

### Research & Benchmarks
- [ExtractBench: Complex Structured Extraction Benchmark (Feb 2026)](https://arxiv.org/abs/2602.12247)
- [ExtractBench GitHub](https://github.com/ContextualAI/extract-bench)
- [Gemini PDF Structured Outputs Tutorial](https://www.philschmid.de/gemini-pdf-to-data)
- [LLMs for Structured Data Extraction from PDFs (Unstract, 2026)](https://unstract.com/blog/comparing-approaches-for-using-llms-for-structured-data-extraction-from-pdfs/)
- [Smart-Hiring CV Extraction Pipeline](https://arxiv.org/abs/2511.02537)
- [Document Parsing Techniques Survey](https://arxiv.org/html/2410.21169v1)

### CV/Resume Parsing
- [OpenResume](https://www.open-resume.com/resume-parser)
- [Resume Parser GitHub Topic](https://github.com/topics/resume-parser)
- [Unstract Resume Parsing Guide](https://unstract.com/blog/guide-to-ai-resume-parsing-with-unstract/)
- [LLM Resume Parsing Guide (Datumo)](https://www.datumo.io/blog/parsing-resumes-with-llms-a-guide-to-structuring-cvs-for-hr-automation)
