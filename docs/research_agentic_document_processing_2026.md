# Agentic Document Processing: State of the Art (March 2026)

*Research report for CViche pipeline modernization*
*Compiled: 2026-03-19*

---

## Executive Summary

The field of agentic document processing has matured significantly since 2024, but remains in a transitional phase. The key finding for the CViche use case is: **a hybrid architecture — an agent orchestrating a minimal pipeline with self-correction loops — is the most practical path forward.** Pure agent architectures are still too expensive and unpredictable for production document extraction, while pure pipelines lack the flexibility to handle the variability in academic CVs.

Three developments are directly relevant to CViche's segmentation and classification problems:

1. **The "segmentation as a join" framework** (SIGMOD/DEEM 2025 best paper) formalizes exactly your problem — joining a document against a taxonomy — and proposes configurable algorithms for it.
2. **Claude's "think" tool** provides a 54% improvement on policy-heavy classification tasks with near-zero implementation effort.
3. **LangGraph's self-correcting extraction loops** offer a proven pattern for iterative validation against your 60-code taxonomy.

---

## 1. Agentic Document Processing Patterns

### 1.1 Production Case Studies

**Genuine production deployments remain rare.** The IntuitionLabs/ZenML survey (2025) found only 5% of organizations report fully integrated agentic deployments, though 57% have agents in production for *some* tasks.

**Stripe's compliance investigation agents** are the most credible production case study. They decomposed complex compliance reviews into bite-sized tasks orchestrated by a directed acyclic graph (DAG), with agents operating on strict "rails." The LLM functions as a worker within a structured workflow, not as an autonomous decision-maker. Results: 26% reduction in average handling time, 96% helpfulness ratings from reviewers. The key insight: **breaking workflows into bite-sized tasks was essential for fitting work within the agent's working memory and making quality evaluation tractable.**
*Source: [ZenML LLMOps Database](https://www.zenml.io/llmops-database/ai-powered-compliance-investigation-agents-for-enhanced-due-diligence), 2025*

**Shopify's Sidekick** evolved from simple tool-calling into an agentic platform handling 30M product classifications daily across 10,000+ categories. They encountered the "tool complexity problem" when scaling from 20 to 50+ tools and solved it with "Just-in-Time instructions" — providing relevant guidance only when needed.
*Source: [ZenML LLMOps Blog](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025), 2025*

### 1.2 Frameworks Compared

| Framework | Strengths | Weaknesses | Best For |
|-----------|-----------|------------|----------|
| **LangGraph** | Graph-based orchestration, built-in self-correction loops, state management, conditional edges | Complex setup, LangChain ecosystem lock-in | Multi-step extraction with validation loops |
| **OpenAI Agents SDK** | Lightweight, production-ready (upgrade from Swarm), structured output via Pydantic, tracing built-in | OpenAI-only, newer ecosystem | Simple agent chains with structured extraction |
| **CrewAI** | Role-based multi-agent teams, Agent Operations Platform (AOP) for production | Higher abstraction = less control, cost concerns with multiple agents | Team-based workflows where roles are clear |
| **PydanticAI** | Type-safe outputs, schema validation built-in, model-agnostic, lightweight | Less orchestration capability | Structured extraction where schema compliance is critical |
| **Claude tool use** | Think tool for reasoning, extended thinking, strong vision, native PDF processing | Anthropic-only, less orchestration framework | Policy-heavy classification, document understanding |
| **LlamaIndex ADW** | LlamaParse integration, reference implementations, agentic document workflows | Commercial dependency (LlamaCloud), newer | End-to-end document automation workflows |

*Sources: [LangGraph docs](https://docs.langchain.com/oss/python/langgraph/workflows-agents), [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/), [CrewAI](https://crewai.com/), [PydanticAI](https://ai.pydantic.dev/), [Anthropic engineering blog](https://www.anthropic.com/engineering/claude-think-tool), [LlamaIndex](https://www.llamaindex.ai/blog/introducing-agentic-document-workflows)*

### 1.3 The "Lost in the Middle" Problem

The foundational finding (Liu et al., 2023; TACL 2024): LLM performance degrades significantly when relevant information appears in the middle of long contexts, even with explicitly long-context models. Performance is highest when relevant information is at the beginning or end.

**Recent mitigations (2025):**

- **Strategic document ordering**: Reranking models position the most relevant content at optimal locations within the context window.
- **Chunked processing with overlap**: Breaking documents into overlapping windows, processing each independently, then merging results.
- **Agent-based iterative retrieval**: DocAgent (EMNLP 2025) extracts a tree-formatted outline first, then uses an interactive reading interface where agents query specific sections, avoiding the need to process the entire document at once.
- **GM-Extract benchmark** (arXiv, Nov 2025): Demonstrated that simply altering how data is *represented* in the context window significantly changes retrieval performance for 7-8B parameter models.

**Relevance to CViche**: Your mega-block problem (20+ items merged from table columns) is likely exacerbated by lost-in-the-middle effects. When a CV has 15+ pages, items in the middle are more likely to be improperly segmented. The solution is not bigger context windows but rather **chunked processing with structural awareness**.

*Sources: [Lost in the Middle (arXiv)](https://arxiv.org/abs/2307.03172), [GM-Extract (arXiv)](https://arxiv.org/html/2511.13900v1), [DocAgent (EMNLP 2025)](https://aclanthology.org/2025.emnlp-main.893/)*

### 1.4 Proven Self-Correction Patterns

**LangGraph's extraction retry loop** is the most mature pattern:
1. LLM extracts structured data against a Pydantic schema
2. Pydantic validates the output
3. If validation fails, error messages are fed back to the LLM as a ToolMessage
4. LLM corrects its output
5. Loop until valid or retry limit hit

Two retry strategies exist:
- **Full regeneration**: LLM regenerates the entire output (simple, but expensive)
- **JSONPatch-based retries**: LLM generates patches to fix specific errors (cheaper for large outputs)

*Source: [LangGraph extraction tutorial](https://langchain-ai.github.io/langgraph/tutorials/extraction/retries/), [Machine Learning Plus](https://machinelearningplus.com/gen-ai/langgraph-structured-output-validation-self-correcting/)*

**Claude's "think" tool** provides a complementary approach — a designated reasoning step within the tool-use loop where Claude can:
- List specific rules that apply to the current classification
- Check if all required information has been collected
- Verify that the planned action complies with all policies

In the airline domain benchmark (tau-bench), the think tool with an optimized prompt achieved a **54% relative improvement** (0.370 to 0.570 pass rate). Implementation is trivial — a single JSON tool definition.

*Source: [Anthropic: The "think" tool](https://www.anthropic.com/engineering/claude-think-tool)*

**TabAgent** (VLDB 2025 Workshop) specifically addresses table extraction with a multi-agent framework: a Semantic Agent captures contextual relationships, a Validation Agent enforces schema constraints, and a shared memory repository stores error patterns from past extractions for iterative refinement.

*Source: [TabAgent (VLDB 2025)](https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/DATAI/DATAI25_6.pdf)*

---

## 2. MCP (Model Context Protocol) for Document Processing

### 2.1 Ecosystem Maturity

MCP has reached significant maturity as of March 2026:

- **97 million monthly SDK downloads**
- **11,880+ community MCP servers** (per PulseMCP directory)
- **110 official/reference servers** in the canonical registry
- **Governance**: Anthropic donated MCP to the Linux Foundation's Agentic AI Foundation (AAIF) in December 2025, co-founded with OpenAI, Block, Google, Microsoft, AWS, Cloudflare, and Bloomberg
- **Specification**: Version 2025-11-25 is current; 2026 roadmap focuses on transport scalability, agent-to-agent communication, and enterprise auth
- **FastMCP 3.0** released January 19, 2026 with component versioning, granular authorization, and OpenTelemetry instrumentation

*Sources: [MCP 2026 Roadmap](http://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/), [Anthropic AAIF announcement](https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation), [PulseMCP](https://www.pulsemcp.com/servers)*

### 2.2 MCP Servers for Academic Data

Several MCP servers exist for academic/scholarly data:

| Server | Data Source | Capabilities |
|--------|-----------|--------------|
| Semantic Scholar MCP | Semantic Scholar API | Paper search with year/field filters, author info, citations, export (BibTeX/APA/MLA) |
| Paper Search MCP | arXiv, PubMed, bioRxiv, medRxiv, Google Scholar, Crossref, OpenAlex, CORE, Europe PMC | Multi-source paper search and download |
| Academic Search MCP | Semantic Scholar + Crossref | Claude Desktop integration for paper access |

**No existing MCP servers were found for document processing taxonomies, CV parsing, or activity classification.** This is a gap — a custom MCP server for your 60-code taxonomy would be a novel contribution.

*Sources: [PulseMCP Semantic Scholar](https://www.pulsemcp.com/servers/jackkuo666-semanticscholar), [Paper Search MCP (GitHub)](https://github.com/openags/paper-search-mcp), [Academic Search MCP (GitHub)](https://github.com/afrise/academic-search-mcp-server)*

### 2.3 Building a Custom MCP Server: Practical Assessment

**Effort**: Minimal. A working MCP server with FastMCP requires approximately 8-15 lines of code for a basic tool. A taxonomy lookup server with 5-10 tools would be approximately 100-200 lines of Python.

**What a CViche taxonomy MCP server could expose**:
- `lookup_taxonomy_code(code)` — return definition, examples, disambiguation rules
- `classify_entry(entry_text, context)` — suggest top-3 codes with confidence
- `get_similar_codes(code)` — return commonly confused codes
- `validate_classification(entry_text, proposed_code)` — check if assignment is reasonable
- `get_section_codes(section_type)` — return valid codes for a given section type

**Framework**: FastMCP 3.0 (decorator-based, Flask/FastAPI-like syntax, auto-generates schemas from type hints and docstrings).

**Deployment**: Can run as a local stdio server (for Claude Desktop) or as an SSE/HTTP server. The official MCP Python SDK 1.2.0+ is required.

*Sources: [FastMCP (GitHub)](https://github.com/jlowin/fastmcp), [MCP Build Server docs](https://modelcontextprotocol.io/docs/develop/build-server), [FastMCP tutorial (Firecrawl)](https://www.firecrawl.dev/blog/fastmcp-tutorial-building-mcp-servers-python)*

### 2.4 MCP vs. Function Calling

| Dimension | Function Calling | MCP |
|-----------|-----------------|-----|
| **Complexity** | Simple, vendor-specific | Standardized, model-agnostic |
| **Scale** | Works well with 5-20 tools | Designed for 100s of tools |
| **Portability** | Tied to one LLM provider | Works across Claude, ChatGPT, Gemini, etc. |
| **Security** | Credentials in application code | Credential isolation at server level |
| **Setup effort** | Lower (inline definitions) | Slightly higher (separate server process) |
| **When to use** | Single agent, few tools, one provider | Multiple agents, many tools, multi-provider |

**For CViche**: Function calling is sufficient today. MCP becomes valuable if you want to: (a) share the taxonomy server across multiple tools/interfaces, (b) switch between Claude and GPT models, or (c) expose the taxonomy to Claude Desktop for interactive development. Most production systems use both — function calling for core extraction, MCP for ancillary tools.

*Sources: [MCP vs Function Calling (Obot)](https://obot.ai/resources/learning-center/mcp-vs-function-calling/), [MCP vs Function Calling (Descope)](https://www.descope.com/blog/post/mcp-vs-function-calling), [MCP vs Function Calling (jztan)](https://blog.jztan.com/mcp-vs-function-calling-ai-agents/)*

---

## 3. Agent vs. Pipeline Architecture

### 3.1 The Hard Numbers

| Metric | Agents | Pipelines |
|--------|--------|-----------|
| **Cost** | Potentially 10x more expensive (multiple LLM calls per task) | Cost-efficient, optimizable |
| **Throughput** | Lower (unsuitable for high-volume batch) | Extremely high (Uber: 10M predictions/sec) |
| **Predictability** | Variable outputs, harder to test | Identical inputs = consistent results |
| **Production adoption (2025)** | ~5% of enterprise apps | 78% of enterprises have dedicated MLOps teams |
| **Projected cancellation** | Gartner: 40% of agentic projects may be canceled by 2027 due to cost overruns | Continuing acceleration |

*Source: [IntuitionLabs: AI Agents vs AI Workflows](https://intuitionlabs.ai/articles/ai-agent-vs-ai-workflow)*

### 3.2 When Agents Outperform Pipelines

Agents excel when:
- Tasks require **dynamic decision-making** based on document content (e.g., "this CV uses a non-standard format, I need to adjust my approach")
- **Multi-turn reasoning** is needed (e.g., "this entry says 'invited lecture' but appears under 'Publications' — I need to consider context")
- **Self-correction** improves output quality (e.g., "my first classification was wrong because I missed that this is a review article, not original research")
- The problem space is **open-ended** and cannot be decomposed into fixed steps

Agents fail when:
- **High throughput** is required (batch processing hundreds of CVs)
- **Costs must be predictable** (each agent call is variable-length)
- **Audit trails are critical** (agent reasoning paths are harder to log deterministically)
- The task is **well-defined** and decomposable (classic pipeline territory)

### 3.3 The Hybrid Pattern: Agent-Orchestrated Pipeline

The emerging consensus is a hybrid where **agents handle orchestration and edge cases** while **pipelines handle the deterministic work**. This is the Stripe pattern:

```
Agent (orchestrator)
  |
  +-- Pipeline Step 1: PDF extraction (deterministic)
  +-- Pipeline Step 2: Section boundary detection (LLM, but constrained)
  +-- Agent Decision: "Is this a table-heavy CV? Use table-specific segmentation"
  +-- Pipeline Step 3: Entry extraction (LLM with structured output)
  +-- Agent Decision: "Entries 14-17 look like mega-blocks. Re-segment."
  +-- Pipeline Step 4: Classification against taxonomy
  +-- Agent Decision: "Entry 23 classified as M2A but context suggests M3A. Re-classify with more context."
  +-- Pipeline Step 5: Output formatting (deterministic)
```

**Key principles from production deployments:**
1. The agent operates on "rails" (DAG structure) — it does not have free-form autonomy
2. Each pipeline step has independent quality evaluation
3. Agent decisions are logged and auditable
4. Damage from a bad agent decision is contained to one step
5. The highest-scoring output is exported, not just the most recent

*Sources: [ZenML LLMOps](https://www.zenml.io/llmops-database/ai-powered-compliance-investigation-agents-for-enhanced-due-diligence), [Self-Correcting Multi-Agent Systems (Medium)](https://medium.com/@sohamghosh_23912/self-correcting-multi-agent-ai-systems-building-pipelines-that-fix-themselves-010786bae2db), [StackAI 2026 Guide](https://www.stackai.com/blog/the-2026-guide-to-agentic-workflow-architectures)*

---

## 4. CV/Resume Parsing: Modern Approaches

### 4.1 Commercial Solutions (2025-2026)

**LandingAI Agentic Document Extraction (ADE)**: The most mature commercial offering. Uses Document Pre-trained Transformer (DPT-2) for layout understanding. Scored 69/100 on a benchmark of agentic extraction tools. Handles tables with merged cells, identifies structural elements (attestations, logos, barcodes), and operates in planning-reflection-correction loops. Available as API/SDK with Snowflake, SAP, Databricks integrations.
*Source: [LandingAI ADE](https://landing.ai/ade), [AIMultiple benchmark](https://aimultiple.com/agentic-document-extraction)*

**LlamaParse v2 (LlamaIndex)**: Tiered parsing system (Fast, Cost Effective, Agentic, Agentic Plus). The "Agentic" tiers use a team of specialized document understanding agents. LlamaSplit Beta can automatically separate bundled documents into distinct sections. Handles 90+ file types including embedded images, complex layouts, multi-page tables, and handwritten notes.
*Source: [LlamaParse v2](https://www.llamaindex.ai/blog/introducing-llamaparse-v2-simpler-better-cheaper), [LlamaIndex ADW](https://www.llamaindex.ai/blog/introducing-agentic-document-workflows)*

**Unstract**: Open-source (AGPL-3.0) agentic document intelligence platform. Uses LLMWhisperer for OCR, then LLMs for extraction. Prompt Studio allows defining extraction schemas in natural language. Can deploy as API or ETL pipeline. Integrates with CrewAI for multi-agent workflows.
*Source: [Unstract (GitHub)](https://github.com/Zipstack/unstract), [Unstract + CrewAI](https://unstract.com/blog/agentic-document-extraction-processing-with-unstract-crew-ai/)*

**RChilli LLM Parser**: Commercial resume parser with taxonomy support. Announced Talent Data Refresh Agent on Oracle Fusion AI Agent Marketplace (Jan 2026) and ServiceNow integration (Dec 2025). Specifically addresses resume/CV parsing with taxonomy-based classification.
*Source: [RChilli](https://www.rchilli.com/solutions/llm-parser)*

### 4.2 Academic Research

**Multi-Agent Resume Screening Framework** (arXiv, April 2025): Four specialized agents (resume extractor, evaluator, summarizer, score formatter) with RAG integration for external knowledge (university rankings, certifications, industry expertise). Achieved performance comparable to human evaluators.
*Source: [arXiv 2504.02870](https://arxiv.org/abs/2504.02870)*

**DocAgent** (EMNLP 2025): Multi-agent framework for long-context document understanding. Extracts a tree-formatted outline, uses an interactive reading interface for targeted retrieval, and includes a reviewer agent that cross-checks responses using complementary sources. Maintains a task-agnostic memory bank across tasks. Bridges the gap to human-level performance while maintaining short context lengths.
*Source: [DocAgent (EMNLP 2025)](https://aclanthology.org/2025.emnlp-main.893/)*

### 4.3 The Segmentation-as-Join Framework (Directly Relevant)

**"Towards a Framework for Hierarchical Text Segmentation using Large Language Models"** — Best paper at DEEM workshop (SIGMOD 2025).

This paper formalizes the *exact* problem CViche faces: breaking a document into segments annotated with labels from a taxonomy of classes. The key insight: **hierarchical segmentation can be viewed as a join between the document and the taxonomy.**

Two algorithms are proposed, inspired by database join operators:
1. **Index join approach**: For each taxonomy code, scan the document for matching segments (taxonomy-first)
2. **Merge-sort join approach**: Process the document linearly, matching each segment to the taxonomy as you go (document-first)

The paper demonstrates that **tailoring the algorithm to the specific use case is crucial** — different CV formats may benefit from different join strategies. Long documents and large taxonomies (your 60 codes) make single-LLM-call approaches unreliable, necessitating decomposition.

**Relevance to CViche**: This framework provides theoretical grounding for your architecture decisions. Your current pipeline is essentially a merge-sort join (process the document linearly, classify as you go). An index join approach (for each of the 60 codes, find all matching entries in the CV) might work better for certain CV formats, particularly table-heavy ones where the document-first approach produces mega-blocks.

*Source: [DEEM/SIGMOD 2025](https://dl.acm.org/doi/10.1145/3735654.3735941)*

### 4.4 Document Parsing Tools Comparison

| Tool | Table Extraction Accuracy | Speed | Open Source | Agentic |
|------|--------------------------|-------|-------------|---------|
| **Docling** | 97.9% (complex tables) | Moderate | Yes | No |
| **LlamaParse v2** | Good (complex layouts can struggle) | ~6 sec regardless of size | No (commercial) | Yes (Agentic tier) |
| **Unstructured** | 100% (simple), 75% (complex) | Moderate | Partial | No |
| **Reducto** | Good | Fast | No | No |
| **LandingAI ADE** | 69/100 benchmark | API-dependent | No | Yes |

*Sources: [Reducto comparison](https://llms.reducto.ai/document-parser-comparison), [Procycons benchmark](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/)*

---

## 5. Recommendations for CViche

### 5.1 Immediate Wins (Low Effort, High Impact)

1. **Add Claude's "think" tool** to your classification step. When classifying entries against the 60-code taxonomy, include a tool that lets the model reason about which code applies before committing. Implementation: ~10 lines of JSON. Expected improvement: 30-54% on ambiguous classifications based on Anthropic's benchmarks.

2. **Implement Pydantic validation loops** for extraction output. Define strict schemas for each taxonomy code's expected fields. When extraction fails validation, feed the error back and retry (up to 3 times). This is the proven LangGraph pattern.

3. **Strategic document chunking** to address mega-blocks. Instead of processing entire pages, use a two-pass approach:
   - Pass 1: Identify section boundaries and structural elements (tables vs. prose vs. lists)
   - Pass 2: For table-heavy sections, process each table independently with table-specific prompts

### 5.2 Medium-Term Architecture (Agent-Orchestrated Pipeline)

Redesign the pipeline as a hybrid with these components:

1. **PDF extraction** (deterministic — keep current approach or adopt Docling for better table handling)
2. **Structure detection agent** — classifies each page/region as table, list, prose, or header. Uses vision capabilities for layout-aware parsing
3. **Segmentation pipeline** — different strategies per structure type:
   - Tables: row-by-row extraction
   - Lists: item-by-item extraction
   - Prose: paragraph-level segmentation with overlap
4. **Classification agent** with think tool — maps each entry to taxonomy codes with self-correction
5. **Validation agent** — cross-checks classifications using:
   - Schema validation (does the entry have the right fields for this code?)
   - Context validation (does this classification make sense given surrounding entries?)
   - Frequency validation (is this an unusual code for this type of CV section?)
6. **Quality orchestrator** — detects mega-blocks, triggers re-segmentation, and manages retry budgets

### 5.3 MCP Server for Taxonomy

Build a lightweight MCP server (~150 lines of Python with FastMCP) that exposes your 60-code taxonomy as tools. This provides:
- Interactive development: Use Claude Desktop to test classifications conversationally
- Portability: Switch between Claude and GPT models without rewriting tool definitions
- Reusability: The web interface, CLI pipeline, and any future agents all share one taxonomy source of truth

### 5.4 What to Avoid

- **Full CrewAI/multi-agent architectures** for the core extraction pipeline — the overhead and unpredictability are not worth it for a well-defined task
- **Replacing your entire pipeline with an agent** — production data shows pipelines are more reliable for structured extraction
- **Paying for commercial ADE tools** (LandingAI, LlamaParse Agentic) when Claude + structured output + self-correction can handle academic CVs well
- **Building for MCP first** — use function calling for core extraction, add MCP only for the taxonomy server and development tooling

---

## 6. Key Research Papers and Resources

| Paper/Resource | Year | Relevance |
|---------------|------|-----------|
| Hierarchical Text Segmentation Framework (DEEM/SIGMOD) | 2025 | Directly models your segmentation-as-join problem |
| DocAgent (EMNLP) | 2025 | Multi-agent long-document understanding with reviewer |
| TabAgent (VLDB Workshop) | 2025 | Multi-agent table extraction with validation |
| Claude "think" tool (Anthropic) | 2025 | Drop-in improvement for classification accuracy |
| Lost in the Middle (TACL) | 2024 | Explains mega-block failure mode |
| GM-Extract benchmark (arXiv) | 2025 | Quantifies context window position effects |
| LangGraph extraction retries (LangChain) | 2025 | Reference implementation for self-correction |
| Multi-Agent Resume Screening (arXiv) | 2025 | RAG-enhanced resume evaluation agents |
| Stripe compliance agents (ZenML) | 2025 | Production hybrid agent-pipeline pattern |

---

## Sources

- [IntuitionLabs: AI Agents vs AI Workflows (2025)](https://intuitionlabs.ai/articles/ai-agent-vs-ai-workflow)
- [ZenML: What 1,200 Production Deployments Reveal (2025)](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025)
- [Stripe Compliance Investigation Agents (ZenML)](https://www.zenml.io/llmops-database/ai-powered-compliance-investigation-agents-for-enhanced-due-diligence)
- [Anthropic: The "think" tool](https://www.anthropic.com/engineering/claude-think-tool)
- [Anthropic: Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)
- [DocAgent (EMNLP 2025)](https://aclanthology.org/2025.emnlp-main.893/)
- [TabAgent (VLDB 2025)](https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/DATAI/DATAI25_6.pdf)
- [Hierarchical Text Segmentation (DEEM/SIGMOD 2025)](https://dl.acm.org/doi/10.1145/3735654.3735941)
- [Lost in the Middle (arXiv/TACL)](https://arxiv.org/abs/2307.03172)
- [GM-Extract (arXiv 2025)](https://arxiv.org/html/2511.13900v1)
- [LangGraph Extraction Retries](https://langchain-ai.github.io/langgraph/tutorials/extraction/retries/)
- [LangGraph Structured Output Self-Correction](https://machinelearningplus.com/gen-ai/langgraph-structured-output-validation-self-correcting/)
- [MCP 2026 Roadmap](http://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [Anthropic Donating MCP to AAIF](https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation)
- [FastMCP (GitHub)](https://github.com/jlowin/fastmcp)
- [FastMCP Tutorial (Firecrawl)](https://www.firecrawl.dev/blog/fastmcp-tutorial-building-mcp-servers-python)
- [MCP vs Function Calling (Obot)](https://obot.ai/resources/learning-center/mcp-vs-function-calling/)
- [MCP vs Function Calling (Descope)](https://www.descope.com/blog/post/mcp-vs-function-calling)
- [Official MCP Registry](https://registry.modelcontextprotocol.io/)
- [PulseMCP Server Directory](https://www.pulsemcp.com/servers)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [LandingAI ADE](https://landing.ai/ade)
- [AIMultiple: Agentic Document Extraction](https://aimultiple.com/agentic-document-extraction)
- [LlamaIndex: Agentic Document Workflows](https://www.llamaindex.ai/blog/introducing-agentic-document-workflows)
- [LlamaParse v2](https://www.llamaindex.ai/blog/introducing-llamaparse-v2-simpler-better-cheaper)
- [Unstract (GitHub)](https://github.com/Zipstack/unstract)
- [CrewAI](https://crewai.com/)
- [PydanticAI](https://ai.pydantic.dev/)
- [Reducto Document Parser Comparison](https://llms.reducto.ai/document-parser-comparison)
- [Multi-Agent Resume Screening (arXiv)](https://arxiv.org/abs/2504.02870)
- [RChilli LLM Parser](https://www.rchilli.com/solutions/llm-parser)
- [LangChain: Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [Claude Extracting Structured JSON Cookbook](https://platform.claude.com/cookbook/tool-use-extracting-structured-json)
- [Hierarchical Text Classification with LLMs (arXiv)](https://arxiv.org/html/2508.04219v1)
- [Semantic Scholar MCP Server (PulseMCP)](https://www.pulsemcp.com/servers/jackkuo666-semanticscholar)
- [Paper Search MCP (GitHub)](https://github.com/openags/paper-search-mcp)
