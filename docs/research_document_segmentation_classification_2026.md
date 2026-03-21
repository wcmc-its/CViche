# AI-Powered Document Segmentation and Classification: State of the Art (March 2026)

**Research Date:** March 19, 2026
**Context:** CViche pipeline for parsing academic faculty CVs (5-40+ pages) into structured data against a ~60-code taxonomy.
**Core problems:** (1) Segmentation failures from diverse formatting, tables, multi-column layouts; (2) Classification accuracy against a large taxonomy.

---

## Table of Contents

1. [Document Layout Understanding](#1-document-layout-understanding)
2. [Classification with Large Taxonomies](#2-classification-with-large-taxonomies)
3. [Hybrid Approaches](#3-hybrid-approaches)
4. [Table Extraction](#4-table-extraction)
5. [Recommendations for CViche](#5-recommendations-for-cviche)
6. [Sources](#sources)

---

## 1. Document Layout Understanding

### 1.1 The Landscape Has Shifted to Multimodal

The defining transition of 2025-2026 is the move from "extract this field" to "understand this document." According to Gartner's 2025 IDP report, 67% of enterprise document processing initiatives are now evaluating agentic approaches over traditional OCR-plus-rules stacks, up from 23% two years ago.

**Source:** [Artificio - The 2026 State of Document AI](https://artificio.ai/blog/document-ai-trends-2026-from-ocr-to-agentic-processing)

### 1.2 Foundation Models for Document Layout

#### LayoutLMv3 (Microsoft, 2022 -- still widely used)
- **Status:** Mature, production-ready via Hugging Face Transformers.
- **Approach:** Unified multimodal pre-training combining text, layout (bounding boxes), and image features.
- **Strengths:** Fine-tunable for form understanding, receipt understanding, document VQA, layout analysis. Strong on DocLayNet benchmarks.
- **Limitations:** Requires OCR as a pre-processing step. Performance degrades on heavily distorted or unconventional layouts. Still the default choice for many production systems.
- **Relevance to CViche:** Could be fine-tuned on academic CV layouts to detect section boundaries. However, requires bounding box data from OCR, which means it doesn't solve the upstream PDF extraction problem on its own.

**Sources:** [LayoutLMv3 on HuggingFace](https://huggingface.co/docs/transformers/en/model_doc/layoutlmv3), [EmergentMind topic](https://www.emergentmind.com/topics/layoutlmv3)

#### Donut (OCR-free Document Understanding Transformer, 2022)
- **Status:** Established; the architecture influenced subsequent models.
- **Approach:** End-to-end vision encoder (Swin Transformer) + text decoder (BART). No OCR needed -- works directly on document images.
- **Strengths:** Eliminates OCR error propagation. Handles multilingual documents.
- **Limitations:** Originally trained on specific document types (receipts, forms). Would require fine-tuning on academic CVs.

**Source:** [Donut on HuggingFace](https://huggingface.co/docs/transformers/en/model_doc/donut)

#### Nougat (Meta, 2023)
- **Status:** Specialized for academic/scientific documents.
- **Approach:** Built on Donut framework. Converts document images to markup text.
- **Strengths:** Purpose-built for scientific PDFs. End-to-end, OCR-free.
- **Limitations:** Optimized for LaTeX-style academic papers, not the diverse formatting of CVs.

**Source:** [Nougat paper](https://arxiv.org/pdf/2308.13418)

#### DocLayout-YOLO (OpenDataLab, 2024-2025)
- **Status:** Active development; strong performance on benchmarks.
- **Approach:** YOLO-v10 based, with document-specific optimizations. Includes DocSynth-300K synthetic training dataset.
- **Strengths:** Real-time inference. Surpasses LayoutLMv3 and DiT on accuracy while matching YOLOv10 speed. Detects headers, text blocks, tables, figures, etc.
- **Limitations:** Detection only (finds regions); doesn't perform text extraction or classification.
- **Relevance to CViche:** Excellent candidate for a lightweight first pass to detect section boundaries, headers, and tables from page images.

**Source:** [DocLayout-YOLO on GitHub](https://github.com/opendatalab/DocLayout-YOLO)

#### PP-DocLayout (PaddlePaddle, 2025)
- **Status:** Production-ready; part of PaddleOCR ecosystem.
- **Approach:** Recognizes 23 types of layout regions. PP-DocLayout-S inference time: 8.1ms per page on T4 GPU.
- **Strengths:** Extremely fast. Supports Chinese and English. Part of a full pipeline.
- **Relevance to CViche:** Could serve as a rapid layout pre-processor.

**Source:** [PP-DocLayout paper](https://arxiv.org/html/2503.17213v1)

#### DLAFormer (Unified Transformer for Layout Analysis)
- **Status:** Research; addresses the multi-task problem jointly.
- **Approach:** Jointly solves layout detection, reading order, logical role assignment, and region relation prediction in a single transformer.
- **Relevance to CViche:** Directly addresses the reading-order problem that causes multi-column CV layouts to interleave incorrectly.

**Source:** [Document Layout Analysis Models overview](https://www.rohan-paul.com/p/state-of-the-art-model-architectures)

#### Recent CVPR 2025 Models
- **Docopilot** (Shanghai AI Lab): +19.9% accuracy vs InternVL2-8B for document-level understanding. Uses Ring Attention and multimodal data packing.
- **DocLayLLM:** Multimodal LLM extension that combines OCR output (text + bounding boxes) with visual features for document understanding.
- **Marten:** Visual QA with mask generation for multimodal document understanding.

**Sources:** [Docopilot CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Duan_Docopilot_Improving_Multimodal_Models_for_Document-Level_Understanding_CVPR_2025_paper.pdf), [DocLayLLM CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Liao_DocLayLLM_An_Efficient_Multi-modal_Extension_of_Large_Language_Models_for_CVPR_2025_paper.pdf)

### 1.3 Vision-Language Models for Document Segmentation

#### Can VLMs Reliably Segment Documents from Page Images?

**The answer in 2026: Yes, but with caveats.**

- **GPT-5.x vision:** GPT-5.2 (Dec 2025) and GPT-5.4 (early 2026) can extract structured data from images, interpret tables, and reason about diagrams. Combined with structured output (JSON schema), they can produce reliable bounding boxes and structured extractions. However, GPT-5.4 with vision is expensive for high-volume processing.
- **Claude Sonnet 4.5/4.6:** Strong at combining visual and textual reasoning, handling documents holistically including captions, headers, labels, and surrounding context. Processes PDFs by rendering pages as images internally. Supports 1M token context.
- **Gemini 2.5 Pro:** Nearly perfect table extraction accuracy on benchmarks but with high latency. Layout parser combines specialized OCR with Gemini's generative capabilities.
- **Open-source VLMs:** Qwen2.5-VL-72B-Instruct rivals proprietary models on document understanding benchmarks. DeepSeek-VL2 and GLM-4.5V also strong contenders with 60% lower inference cost than proprietary options.

**Critical finding:** VLMs still struggle on "layout-heavy and noisy visual inputs." Models tested including GPT-4V, Claude, Gemini, and Bard performed poorly on complex layout reasoning and structural interpretation tasks (as of mid-2025 evaluations).

**Source:** [Top VLMs in 2026 - DataCamp](https://www.datacamp.com/blog/top-vision-language-models), [GPT-5 Vision Tests - Roboflow](https://blog.roboflow.com/gpt-5-vision-multimodal-evaluation/)

#### Vision-Guided Chunking (2025)

A notable 2025 paper, "Vision-Guided Chunking Is All You Need" (arXiv 2506.16035), demonstrates that using LMMs to process PDF page images for chunking produces 5x more precise chunks than text-only approaches, improving RAG retrieval accuracy by 14%. Key innovation: cross-batch context preservation for tables spanning multiple pages.

**Source:** [Vision-Guided Chunking paper](https://arxiv.org/abs/2506.16035)

### 1.4 Production Document Parsing Tools (2025-2026)

| Tool | Type | Table Accuracy | Speed | Cost | Notes |
|------|------|---------------|-------|------|-------|
| **Docling** (IBM) | Open-source | 97.9% | Moderate | Free | Uses DocLayNet + TableFormer. Best open-source option. |
| **Marker** | Open-source | Good | 122 pages/sec (H100) | Free | Uses LayoutLMv3 + Surya OCR. Optional LLM mode. |
| **Reducto** | Commercial API | High | Fast | Enterprise | Vision-first hybrid pipeline. SOC2/HIPAA. |
| **LlamaParse** | Commercial API | Good | Fast | $0.003/page | Good default for most use cases. Multiple output formats. |
| **Unstructured.io** | Open-source + API | Good | Fast | Free/paid | Layout-aware parsing. Strong chunking strategies. |
| **Mistral OCR 3** | Commercial API | High | 2000 pages/min | $1-2/1K pages | Understands text, images, tables, equations. |
| **olmOCR** (Allen AI) | Open-source | Good | Moderate | Free | Designed for LLM training data preparation. |

**Key benchmark results (2025):**
- Docling leads on complex table extraction (97.9% accuracy on sustainability reports)
- Reducto: up to 20% higher accuracy on real-world documents vs LlamaParse
- TableFormer: 93.6% average accuracy vs Tabula (67.9%) and Camelot (73.0%)

**Sources:** [Docling GitHub](https://github.com/docling-project/docling), [PDF Data Extraction Benchmark 2025](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/), [Reducto vs LlamaParse](https://llms.reducto.ai/reducto-vs-llamaparse)

### 1.5 Relevance to CViche's Segmentation Problem

**Current approach:** pdfplumber text extraction, then LLM-based segmentation of the text stream.

**Why this fails on tables:** pdfplumber uses rule-based methods for table extraction. When CVs use tables for layout (common for multi-column sections like grants, courses taught, committee service), pdfplumber flattens the table structure into a text stream, losing cell boundaries and column alignment. The LLM then receives garbled text and cannot reliably separate individual entries, producing "mega-blocks."

**What would help:**
1. **Replace pdfplumber with Docling or Marker** for PDF extraction. Both use deep learning layout models (DocLayNet/LayoutLMv3) that understand table structures and preserve them as structured data rather than flattened text.
2. **Add a vision pass** using page images. Send each page as an image to a VLM (Claude vision, GPT-5 vision, or Qwen2.5-VL) alongside the text extraction, asking it to identify section boundaries and table structures. This catches formatting that text-only extraction misses.
3. **Use DocLayout-YOLO or PP-DocLayout** as a fast first pass to detect tables, headers, and section boundaries from page images before feeding content to the LLM.

---

## 2. Classification with Large Taxonomies

### 2.1 Fine-Tuning vs. In-Context Learning vs. RAG Classification

This is one of the best-studied areas in recent NLP research. Key findings:

#### Fine-tuning dominates for fixed taxonomies

Research published in 2025 (Chae & Davidson, SAGE) comparing zero-shot, few-shot, fine-tuning, and instruction-tuning found:
- **Fine-tuned smaller models are competitive** with larger prompted models and much cheaper to run.
- Fully fine-tuned decoder LLMs (Llama3-70B) outperform encoder models (RoBERTa-large) on classification.
- **Quantized LoRA fine-tuning** of 3B-parameter models can compete with or exceed accuracy of much larger models.

**Source:** [Chae & Davidson 2025 - LLMs for Text Classification](https://journals.sagepub.com/doi/10.1177/00491241251325243)

#### In-context learning limitations

- Costly, slow, and limited by context window.
- Sensitive to example quality and variability.
- Increasing the number of examples can actually degrade performance on small instruct models.
- For a 60-category taxonomy, fitting good examples for each category strains context limits.

**Source:** [Advancing Text Classification through LLM Fine-tuning](https://arxiv.org/abs/2412.08587)

#### The small model advantage for classification

A direct comparison for requirements classification (arXiv 2510.21443) found:
- The best SLM (Llama-3-8B fine-tuned) achieved F1 within 0.02 of the best LLM.
- SLMs offer better privacy, faster inference, and lower cost.
- **Fine-tuned 7B-13B models match or exceed GPT-4 performance at 1/10th the cost.**

**Source:** [Does Model Size Matter? Requirements Classification](https://arxiv.org/html/2510.21443v1)

### 2.2 Embedding-Based Classification vs. LLM Prompting

A landmark 2025 empirical study directly compared these approaches:

**"Beyond the Hype: Embeddings vs. Prompting for Multiclass Classification Tasks"** (arXiv 2504.04277, Kokkodis et al.)

Key findings:
- **Embeddings approach had 49.5% higher accuracy** than the best LLM prompt-based approach.
- Embeddings were **14x faster** for images and **81x faster** for text.
- Embeddings were **up to 10x cheaper** under realistic deployment assumptions.
- Results were validated in production via A/B testing.

**However**, this was on a domain with proprietary training data. The advantage comes from having labeled examples that capture the specific taxonomy's semantics.

**Implication for CViche:** If you have or can build a labeled dataset mapping CV entries to your ~60 taxonomy codes (even a few hundred examples), an embedding-based classifier could substantially outperform the current LLM prompt approach in both accuracy and speed.

**Source:** [Beyond the Hype paper](https://arxiv.org/abs/2504.04277)

### 2.3 Reasoning Models for Classification

#### Extended thinking / chain-of-thought

- **Claude 3.7 Sonnet extended thinking:** 84.8% on GPQA Diamond (vs 68.0% standard mode) -- a 25% improvement from thinking.
- **OpenAI o3:** 69.1% on SWE-Bench. Strong on tasks requiring multi-step reasoning.
- Performance improvements are logarithmic with thinking tokens -- diminishing returns after a point.

**For classification specifically:** Reasoning models help most when the classification requires understanding nuanced distinctions (e.g., "Is this a 'clinical service' or a 'professional development' activity?"). They help less for clear-cut cases.

**Recommendation:** Use extended thinking / reasoning selectively -- route confident classifications through a fast path and only engage reasoning for ambiguous cases.

**Sources:** [Reasoning LLMs comparison - WorkOS](https://workos.com/blog/reasoning-llms), [Claude extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)

### 2.4 Structured Output for Classification Reliability

OpenAI's structured output with constrained decoding achieves **100% schema compliance** -- the model can only output tokens that conform to the supplied JSON schema. For classification:
- Define taxonomy codes as an enum in the schema.
- The model is physically constrained to output only valid taxonomy codes.
- Eliminates hallucinated categories entirely.

**This is directly applicable to CViche.** Instead of validating post-hoc that the LLM returned a valid taxonomy code, constrain it at generation time.

**Source:** [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

### 2.5 TnT-LLM: Microsoft's Taxonomy + Classification Framework

Microsoft's TnT-LLM (KDD 2024) provides a two-phase approach highly relevant to CViche:

1. **Phase 1 -- Taxonomy Generation:** LLM iteratively produces and refines a label taxonomy through multi-stage reasoning.
2. **Phase 2 -- Classification at Scale:** LLM labels training data, then a lightweight supervised classifier (e.g., logistic regression on embeddings) is trained for production inference.

This hybrid approach achieves a "favorable balance between accuracy and efficiency." The key insight: **use the expensive LLM to generate high-quality training labels, then deploy a cheap, fast classifier.**

**Source:** [TnT-LLM paper](https://arxiv.org/abs/2403.12173)

---

## 3. Hybrid Approaches

### 3.1 Two-Stage Segmentation: Layout Model + LLM

The most promising architecture for CViche combines:

1. **Fast layout detection** (DocLayout-YOLO, PP-DocLayout, or Docling's layout model): Runs on page images in milliseconds. Outputs bounding boxes for headers, text blocks, tables, figures, lists. This gives you structural scaffolding.

2. **LLM-based semantic segmentation**: Uses the layout detection output to guide segmentation. Instead of asking the LLM to find section boundaries in raw text, you provide it with pre-detected regions and ask it to classify and merge them into logical sections.

This architecture is validated by the "Segmenting Documents with LLMs and Multimodal Document AI" work from Roots.ai, which found that multimodal LayoutLMv3 models "drastically outperform" text-only approaches at document-level segmentation.

**Source:** [Roots.ai - Segmenting Documents](https://www.roots.ai/blog/segmenting-documents-with-llms-and-multimodal-document-ai-part-2)

### 3.2 Lightweight Segmentation + Expensive Classification

The cost-optimization pattern emerging in 2025-2026:

1. **Segmentation:** Use an open-source layout model (free, fast, local) or a cheap API (Docling, LlamaParse at $0.003/page).
2. **Classification:** Use embeddings + a small fine-tuned classifier for clear cases (fast, cheap). Route ambiguous cases to a reasoning model (expensive but accurate).

This "confidence-based routing" pattern is described in the Scikit-LLM production article: use a fast, small traditional model first, and only route uncertain or high-complexity inputs to the costly LLM.

**Source:** [Scikit-LLM for Scalable Text Classification](https://afafathar.medium.com/productionizing-hybrid-ai-a-technical-deep-dive-into-scikit-llm-for-scalable-text-classification-a0cba646f2f8)

### 3.3 Multi-Stage Classification Pipeline

Research on multi-stage LLM classification pipelines (2025) describes architectures directly relevant to CViche's taxonomy problem:

- **Coarse-to-fine prediction:** First classify into broad categories (e.g., "Publications," "Teaching," "Service"), then into specific taxonomy codes within that category. This hierarchical approach reduces the search space at each step.
- **Taxonomy-embedded transition layers:** Enforce consistency between hierarchical levels using transition matrices.
- **Inference-retrieval-reranking:** A three-stage pipeline where initial classification is followed by retrieval of similar labeled examples, then reranking to finalize the assignment.

**Source:** [Multi-Stage LLM Classification Pipeline - EmergentMind](https://www.emergentmind.com/topics/multi-stage-llm-based-classification-pipeline)

### 3.4 Embeddings for Initial Classification, LLM for Disambiguation

A practical hybrid:

1. **Embed each CV entry** using a modern embedding model (e.g., text-embedding-3-large, or open-source Mistral-7B embeddings).
2. **Compute cosine similarity** against pre-embedded taxonomy category descriptions.
3. **If top-1 similarity >> top-2:** Auto-assign with high confidence (cheap, fast).
4. **If top scores are close:** Route to LLM with extended thinking for disambiguation, providing the top-N candidate categories and asking it to reason about the correct assignment.

**Caveat on cosine similarity:** Research shows that cosine similarity can yield "arbitrary and meaningless similarities" for some embedding models. Use carefully calibrated thresholds and validate against labeled data.

**Sources:** [Is Cosine-Similarity Really About Similarity?](https://arxiv.org/html/2403.05440v1), [Embeddings vs Prompting](https://arxiv.org/abs/2504.04277)

### 3.5 Agentic Document Processing

The 2025-2026 trend toward "agentic" document processing is relevant:

- **LlamaIndex Agentic Document Workflows (ADW):** Combines LlamaParse, LlamaCloud, and agentic orchestration. Documents flow through classification, extraction, validation, and synthesis steps, each orchestrated by AI agents.
- **UiPath IXP:** Agents call document models on-demand during workflow execution.
- **Key principle:** Multi-step workflows where each step can make decisions based on document content -- not just analyze it.

**For CViche:** An agentic approach could handle the pipeline as: (1) Extract layout, (2) Segment into entries, (3) Classify each entry, (4) Validate classifications against neighbors and context, (5) Request human review for low-confidence items.

**Source:** [Agentic Document Extraction - AIMultiple](https://research.aimultiple.com/agentic-document-extraction/)

---

## 4. Table Extraction

### 4.1 The Table Problem is CViche's Biggest Pain Point

Tables in CVs cause the most severe segmentation failures. Faculty frequently use tables for:
- Multi-column layouts (date | activity | role)
- Grants (funding agency | amount | years | title)
- Courses taught (course # | title | semester | enrollment)
- Committee service (committee | role | years)

When pdfplumber flattens these, each row's cells get concatenated with adjacent rows, producing "mega-blocks."

### 4.2 Specialized Table Extraction: Current State

#### HTTD (Hierarchical Transformer for Table Detection, 2025)
- Swin-L Transformer backbone
- **96.98% precision** on ICDAR-2019, **96.43%** on TNCR
- State-of-the-art for table detection

#### TableFormer (IBM, used by Docling)
- **93.6% average accuracy** on large table banks
- Significantly outperforms Tabula (67.9%) and Camelot (73.0%)
- Handles complex structures: merged cells, spanning headers

#### TATR (Table Transformer, based on DETR)
- Mainstream approach for table detection + structure recognition
- Well-supported in production via Hugging Face

#### TSRFormer (Table Structure Recognition)
- Formulates table separation line prediction as line regression
- Addresses DETR's slow convergence issue

**Source:** [HTTD paper](https://www.mdpi.com/2227-7390/13/2/266), [PDF Data Extraction Benchmark](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/)

### 4.3 Vision-Based vs. Text-Based Table Extraction

| Approach | Accuracy | Speed | Handles Scans? | Complex Tables? |
|----------|----------|-------|----------------|-----------------|
| pdfplumber (rule-based) | 67-96%* | Fast | No | Poor |
| Camelot (rule-based) | 73% avg | Fast | No | Poor |
| Tabula (rule-based) | 67.9% avg | Fast | No | Poor |
| TableFormer (DL) | 93.6% avg | Moderate | Yes | Good |
| HTTD (DL) | 96.98% | Moderate | Yes | Excellent |
| Gemini 2.5 Pro (VLM) | ~100% data accuracy | Slow | Yes | Good |
| GPT-5 vision (VLM) | High | Slow | Yes | Good |

*pdfplumber accuracy varies widely by document type -- 96% on well-formatted tables, much lower on complex layouts.

**Key insight:** Vision-based approaches (both specialized models and VLMs) substantially outperform text-based tools on complex table layouts. For CViche, the "complex layout" case is exactly the failure mode.

**Sources:** [Table Extraction Showdown](https://boringbot.substack.com/p/pdf-table-extraction-showdown-docling), [Gemini 2.0 Table Extraction](https://medium.com/@sahil0094/how-gemini-2-0-be927d57338a)

### 4.4 Practical Table Extraction for CViche

**Recommended approach:**

1. **Docling** as the primary PDF parser: Its TableFormer model preserves table structure as HTML/structured data rather than flattened text. This directly addresses the mega-block problem.

2. **Fallback to VLM vision**: For tables that Docling's TableFormer doesn't handle well, render the page as an image and send to Claude vision or GPT-5 vision with a structured output schema asking it to extract rows.

3. **Post-processing validation**: Compare text-based and vision-based extractions. Flag discrepancies for review.

---

## 5. Recommendations for CViche

### 5.1 High-Impact Changes (Recommended Order)

#### Change 1: Replace pdfplumber with Docling for PDF Extraction
- **Impact:** Directly addresses the 12% coverage failure from table mega-blocks.
- **Effort:** Moderate -- Docling has a clean Python API, returns structured JSON.
- **What it gives you:** Table structure preservation (TableFormer), layout detection (DocLayNet), reading order preservation, multi-column handling.
- **Risk:** Docling may handle some edge-case CVs differently than pdfplumber. Needs validation against your test set.

#### Change 2: Add Constrained Structured Output for Classification
- **Impact:** Eliminates invalid taxonomy codes from classification output.
- **Effort:** Low -- switch to OpenAI structured output with enum constraints, or use Pydantic + Anthropic tool_use.
- **What it gives you:** 100% valid taxonomy codes. No more post-hoc validation for code validity.

#### Change 3: Build an Embedding-Based Classifier with LLM Fallback
- **Impact:** Based on research, could improve classification accuracy by up to 49.5% over pure LLM prompting while reducing cost and latency.
- **Effort:** Moderate -- requires building a labeled dataset of CV entries mapped to taxonomy codes.
- **Architecture:**
  1. Embed each CV entry with text-embedding-3-large (or open-source alternative).
  2. Pre-embed each taxonomy category with its description + examples.
  3. For each entry, compute similarity to all categories.
  4. If top-1 >> top-2 by threshold: auto-assign (fast path, ~90% of entries).
  5. If ambiguous: route to LLM with extended thinking, providing top-5 candidates.

#### Change 4: Add Vision Pass for Complex Pages
- **Impact:** Catches formatting that text extraction misses. Particularly valuable for multi-column layouts and tables used for formatting.
- **Effort:** Moderate -- render pages as images, send to VLM.
- **Architecture:**
  1. Detect pages with potential complexity (multiple columns, tables, irregular formatting) using DocLayout-YOLO or Docling's layout detector.
  2. For flagged pages only, send page image to Claude vision / GPT-5 vision.
  3. Ask VLM to identify section boundaries and extract table structures.
  4. Merge vision-based and text-based segmentation results.

#### Change 5: Implement Hierarchical Classification
- **Impact:** Reduces the 60-category classification problem to a series of smaller problems.
- **Effort:** Low-moderate -- restructure the taxonomy into a hierarchy (you likely already have one).
- **Architecture:**
  1. First pass: classify into ~10 broad categories (Publications, Teaching, Service, etc.).
  2. Second pass: classify within the broad category into specific codes.
  3. Each pass has fewer options, reducing confusion.

### 5.2 Cost-Performance Tradeoffs

| Approach | Accuracy | Cost/CV | Latency | Notes |
|----------|----------|---------|---------|-------|
| Current (pdfplumber + LLM) | Baseline | $$ | Moderate | 12% coverage failure on tables |
| Docling + LLM | +15-25% | $$ | Moderate | Better segmentation |
| Docling + Embeddings + LLM fallback | +30-50% | $ | Fast | Majority handled by embeddings |
| Docling + Vision + Embeddings + LLM | +40-60% | $$$ | Slow | Best accuracy, highest cost |
| Fine-tuned 8B model | +30-40% | ¢ | Fast | Requires labeled training data |

### 5.3 What NOT to Do

1. **Don't fine-tune a VLM on CV layout detection** unless you have 1000+ annotated CV pages. The generic models (DocLayout-YOLO, Docling) work well enough out of the box.

2. **Don't use VLMs for every page.** Vision processing is 10-50x more expensive than text processing. Use it selectively for pages flagged as complex.

3. **Don't increase the number of post-classification validators beyond 10.** The current 70% error cascade suggests the validators are fixing symptoms, not causes. Better initial classification (via embeddings or fine-tuning) is a more effective investment.

4. **Don't build your own table extraction model.** Use Docling's TableFormer or a commercial API. The benchmarks clearly show that purpose-built table models vastly outperform custom solutions.

### 5.4 Implementation Roadmap

**Phase 1 (1-2 weeks):** Replace pdfplumber with Docling. Validate on your test set. Measure impact on table-heavy CVs.

**Phase 2 (1-2 weeks):** Add structured output constraints for classification. Implement hierarchical classification (broad category first, then specific code).

**Phase 3 (2-4 weeks):** Build embedding-based classifier. Create labeled dataset from existing validated outputs. Implement confidence-based routing to LLM fallback.

**Phase 4 (2-3 weeks):** Add selective vision pass for complex pages. Integrate DocLayout-YOLO or Docling's layout detector for complexity scoring.

**Phase 5 (ongoing):** Fine-tune a small model (Llama-3-8B or similar) on your accumulated labeled data. This becomes your primary classifier, with the LLM as fallback only.

---

## Sources

### Document Layout Understanding
- [The 2026 State of Document AI - Artificio](https://artificio.ai/blog/document-ai-trends-2026-from-ocr-to-agentic-processing)
- [State-of-the-Art Model Architectures for Document Layout Analysis](https://www.rohan-paul.com/p/state-of-the-art-model-architectures)
- [LayoutLMv3 on HuggingFace](https://huggingface.co/docs/transformers/en/model_doc/layoutlmv3)
- [LayoutLMv3 - EmergentMind](https://www.emergentmind.com/topics/layoutlmv3)
- [Donut on HuggingFace](https://huggingface.co/docs/transformers/en/model_doc/donut)
- [Nougat paper - arXiv](https://arxiv.org/pdf/2308.13418)
- [DocLayout-YOLO - GitHub](https://github.com/opendatalab/DocLayout-YOLO)
- [PP-DocLayout paper](https://arxiv.org/html/2503.17213v1)
- [DocLayNet dataset](https://huggingface.co/datasets/docling-project/DocLayNet)
- [Docopilot - CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Duan_Docopilot_Improving_Multimodal_Models_for_Document-Level_Understanding_CVPR_2025_paper.pdf)
- [DocLayLLM - CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Liao_DocLayLLM_An_Efficient_Multi-modal_Extension_of_Large_Language_Models_for_CVPR_2025_paper.pdf)

### Vision-Language Models and Document Understanding
- [Top 10 Vision Language Models in 2026 - DataCamp](https://www.datacamp.com/blog/top-vision-language-models)
- [GPT-5 Vision Tests - Roboflow](https://blog.roboflow.com/gpt-5-vision-multimodal-evaluation/)
- [GPT-5.2 Official Release](https://www.datastudios.org/post/gpt-5-2-official-release-capabilities-context-window-model-variants-pricing-and-workflow-power)
- [Claude Sonnet 4.5 Multimodality](https://www.datastudios.org/post/claude-sonnet-4-5-multimodality-vision-audio-document-understanding-and-context-integration)
- [Claude 4.6 - What's New](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-6)
- [Vision-Guided Chunking paper - arXiv 2506.16035](https://arxiv.org/abs/2506.16035)
- [Multimodal AI Open-Source VLMs - BentoML](https://www.bentoml.com/blog/multimodal-ai-a-guide-to-open-source-vision-language-models)

### Document Parsing Tools
- [Docling - GitHub](https://github.com/docling-project/docling)
- [Docling Guide - DataCamp](https://www.datacamp.com/tutorial/docling)
- [Marker - GitHub](https://github.com/datalab-to/marker)
- [Reducto vs LlamaParse](https://llms.reducto.ai/reducto-vs-llamaparse)
- [Best LLM-Ready Document Parsers 2025 - Reducto](https://llms.reducto.ai/best-llm-ready-document-parsers-2025)
- [PDF Data Extraction Benchmark 2025](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/)
- [PDF Table Extraction Showdown](https://boringbot.substack.com/p/pdf-table-extraction-showdown-docling)
- [Unstructured.io - GitHub](https://github.com/Unstructured-IO/unstructured)
- [Mistral OCR 3](https://mistral.ai/news/mistral-ocr-3)
- [olmOCR - GitHub](https://github.com/allenai/olmocr)
- [OmniDocBench - CVPR 2025](https://github.com/opendatalab/OmniDocBench)

### Classification Research
- [LLMs for Text Classification: From Zero-Shot to Instruction-Tuning - Chae & Davidson 2025](https://journals.sagepub.com/doi/10.1177/00491241251325243)
- [Beyond the Hype: Embeddings vs. Prompting - arXiv 2504.04277](https://arxiv.org/abs/2504.04277)
- [Fine-Tuning Causal LLMs: Embedding-Based vs. Instruction-Based - arXiv 2512.12677](https://arxiv.org/html/2512.12677v1)
- [TnT-LLM: Text Mining at Scale - Microsoft Research](https://www.microsoft.com/en-us/research/publication/tnt-llm-text-mining-at-scale-with-large-language-models/)
- [Does Model Size Matter? Requirements Classification](https://arxiv.org/html/2510.21443v1)
- [Advancing Text Classification through LLM Fine-tuning](https://arxiv.org/abs/2412.08587)
- [SLM vs LLM: Accuracy, Latency, Cost Trade-Offs 2026](https://labelyourdata.com/articles/llm-fine-tuning/slm-vs-llm)

### Reasoning Models
- [Reasoning LLMs: o1, Claude 3.7, DeepSeek R1 - WorkOS](https://workos.com/blog/reasoning-llms)
- [Claude Extended Thinking - Anthropic](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)

### Hybrid and Multi-Stage Approaches
- [Multi-Stage LLM Classification Pipeline - EmergentMind](https://www.emergentmind.com/topics/multi-stage-llm-based-classification-pipeline)
- [Scikit-LLM for Scalable Text Classification](https://afafathar.medium.com/productionizing-hybrid-ai-a-technical-deep-dive-into-scikit-llm-for-scalable-text-classification-a0cba646f2f8)
- [LLM-Enhanced Semantic Text Segmentation](https://www.mdpi.com/2076-3417/15/19/10849)
- [Segmenting Documents with LLMs and Multimodal Document AI](https://www.roots.ai/blog/segmenting-documents-with-llms-and-multimodal-document-ai-part-2)
- [Agentic Document Extraction - AIMultiple](https://research.aimultiple.com/agentic-document-extraction/)
- [LlamaIndex Agentic Document Workflows](https://www.llamaindex.ai/blog/introducing-agentic-document-workflows)

### Structured Output
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Constrained Decoding Guide](https://www.aidancooper.co.uk/constrained-decoding/)
- [LLM Structured Output in 2026](https://dev.to/pockit_tools/llm-structured-output-in-2026-stop-parsing-json-with-regex-and-do-it-right-34pk)

### Table Extraction
- [HTTD: Hierarchical Transformer for Table Detection](https://www.mdpi.com/2227-7390/13/2/266)
- [RAPTOR: Refined Approach for Product Table Object Recognition](https://arxiv.org/abs/2502.14918)
- [Benchmarking Table Extraction - ACL 2025](https://aclanthology.org/2025.xllm-1.2.pdf)
- [Gemini 2.0 Table Extraction Benchmarks](https://medium.com/@sahil0094/how-gemini-2-0-be927d57338a)
- [PdfTable: Deep Learning-Based Table Extraction](https://arxiv.org/abs/2409.05125)

### Embeddings and Semantic Similarity
- [Is Cosine-Similarity Really About Similarity?](https://arxiv.org/html/2403.05440v1)
- [LLMs are Also Effective Embedding Models](https://arxiv.org/abs/2412.12591)
- [Best Open-Source Embedding Models Benchmarked](https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/)
