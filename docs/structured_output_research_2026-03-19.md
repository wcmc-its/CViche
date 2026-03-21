# Structured Output / JSON Schema Enforcement for Document Segmentation & Classification

**Research Date:** March 19, 2026
**Context:** CViche pipeline -- faculty CV parsing with ~60-code taxonomy, 9 LLM calls across 12 stages
**Researcher:** Claude (deep research agent)

---

## Executive Summary

All three major providers (OpenAI, Anthropic, Google) now offer grammar-constrained structured outputs that guarantee JSON schema conformance. However, each has materially different limits, and **none of them cleanly supports the "classify each entry as one of 60 codes, then extract code-specific fields" pattern in a single call** without workarounds. The best path for CViche is likely OpenAI GPT-5.x with its 128k output token limit, 1,000-enum cap, and `anyOf` support with discriminators -- but significant schema design work is required.

---

## 1. Structured Output Capabilities by Provider

### 1.1 OpenAI (GPT-5.x / GPT-4o series)

**Mechanism:** Context-Free Grammar (CFG) engine constrains token generation at inference time. Schemas are compiled into a grammar that prohibits generating tokens outside the schema.

**Current limits (post-2025 raise):**

| Constraint | Old Limit | Current Limit |
|---|---|---|
| Object properties (total across schema) | 100 | **5,000** |
| Enum values (total across schema) | 500 | **1,000** |
| Characters in strings | 15,000 | **120,000** |
| Total chars across enum strings (>250 values) | 7,500 | **15,000** |
| Nesting depth | 5 | **5** (unchanged) |
| Max output tokens (GPT-5.4) | -- | **128,000** |
| Context window (GPT-5.4) | -- | **400,000** |

**Source:** [OpenAI Developer Community - Limits Raised](https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593), [OpenAI Structured Outputs Docs](https://platform.openai.com/docs/guides/structured-outputs)

**Key schema requirements:**
- All fields must be `required`; optional fields simulated via `"type": ["string", "null"]`
- `additionalProperties` must be `false` on all objects
- `anyOf` is supported but objects in an `anyOf` **must not share identical first keys** -- requires a discriminator field with unique first position
- `minLength`, `maximum`, `pattern`, and other validation keywords are **not enforced** by the grammar (silently ignored)
- Lark grammar / regex constrained generation also available (GPT-5+) for custom DSLs

**New in GPT-5+:**
- Responses API replaces Chat Completions as the primary interface
- Schema goes under `text.format.type: "json_schema"` (not `response_format`)
- Custom tools can accept raw text (not just JSON) via `"type": "custom"` with Lark grammars
- 128k output tokens is a major upgrade for variable-length array extraction

**Source:** [GPT-5 Model Docs](https://platform.openai.com/docs/models/gpt-5), [GPT-5 Cookbook](https://cookbook.openai.com/examples/gpt-5/gpt-5_new_params_and_tools)

### 1.2 Anthropic Claude (Sonnet 4.5 / Opus 4.5+)

**Mechanism:** Compiles JSON schema into a grammar; constrains token generation during inference. Grammar is cached for 24 hours after first compilation (100-300ms overhead on first call).

**Launch:** November 14, 2025 (public beta); now GA on Claude Sonnet 4.5, Opus 4.5, Haiku 4.5, and Claude 4.6 models.

**Limits:**

| Constraint | Limit |
|---|---|
| Max output tokens | **64,000** |
| Context window | **1,000,000** (with beta header for >200k) |
| Nesting depth | Recommended **3 or fewer** (no hard cap documented) |
| Properties | Not explicitly documented; governed by grammar compilation timeout |
| Enum values | Not explicitly documented; governed by grammar size |
| Grammar compilation timeout | **180 seconds** |

**Critical limitations:**
- **Does NOT support `oneOf` at top level** -- returns error "Schema type 'oneOf' is not supported"
- **`anyOf` support is limited** -- complex compositions can trigger "compiled grammar is too large" errors
- **`allOf` with `$ref` is NOT supported**
- **`prefixItems` NOT supported**
- Each optional parameter roughly **doubles** a portion of the grammar's state space
- Limits apply to the **combined total** across all strict schemas in a single request (e.g., 4 tools with 6 optional params each = 24 optional params total)

**Workarounds documented by Anthropic:**
1. Mark only critical tools as strict; rely on Claude's natural adherence for simpler tools
2. Make parameters required instead of optional (reduce grammar state space)
3. Flatten nested structures
4. Split complex schemas across multiple requests or sub-agents
5. SDKs can auto-transform unsupported features (strips them and adds constraint descriptions)

**Source:** [Claude Structured Outputs Docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [GitHub Issue #1185](https://github.com/anthropics/anthropic-sdk-python/issues/1185), [Claude Blog](https://claude.com/blog/structured-outputs-on-the-claude-developer-platform)

### 1.3 Google Gemini (2.5 Pro / 3.x)

**Mechanism:** Constrained decoding with schema compilation. Supports JSON Schema with recent expansions.

**Limits:**

| Constraint | Limit |
|---|---|
| Max output tokens (2.5 Pro / 3 Pro) | **65,536** |
| Context window (2.5 Pro) | **1,000,000** |
| Enum values | **~120** (hard limit) |
| Nesting | Rejected at "too many states" |

**Recent improvements (Nov 2025+):**
- Added `anyOf`, `$defs/$ref` for recursive structures
- Added `minimum`/`maximum` value constraints
- Property ordering now preserved (matches schema key order)
- JSON Schema keywords now supported across all Gemini 2.5+ models
- Gemini 3 models can combine structured outputs with Google Search grounding, URL Context, and Code Execution

**Critical limitations for CViche:**
- **Enum limit of ~120 values is a showstopper** for the 60-code taxonomy if codes have long names or if the schema includes sub-codes
- "Too many states" errors from: long property names, long array length limits, enums with many values, objects with many optional properties
- Shortening property/enum names is the recommended workaround, but fundamentally limits expressiveness
- Open issue requesting larger enum support: [GitHub #950](https://github.com/googleapis/python-genai/issues/950)

**Source:** [Gemini Structured Output Docs](https://ai.google.dev/gemini-api/docs/structured-output), [Google Blog - Improving Structured Outputs](https://blog.google/technology/developers/gemini-api-structured-outputs/), [Gemini API Forum](https://discuss.ai.google.dev/t/max-number-of-enums-in-response-schema-for-structured-output/40860)

---

## 2. Can You Do "Classify + Extract Per-Code Fields" in a Single Call?

This is the central design question for CViche: can a single LLM call take an entire CV (or section), classify each entry against one of 60 taxonomy codes, and then extract fields appropriate to that code's schema?

### 2.1 The Schema Design Challenge

This requires a **discriminated union** (polymorphic schema): an array of entries where each entry has a `taxonomy_code` enum field, and the remaining fields depend on which code was selected.

**In JSON Schema terms:**
```json
{
  "type": "array",
  "items": {
    "anyOf": [
      { "properties": { "code": {"const": "S1"}, "title": {...}, "journal": {...}, ... } },
      { "properties": { "code": {"const": "S4"}, "title": {...}, "book_title": {...}, ... } },
      ...60 variants...
    ]
  }
}
```

### 2.2 Provider-Specific Feasibility

| Provider | Feasible? | Blocking Issues |
|---|---|---|
| **OpenAI GPT-5.x** | **Partially yes** | `anyOf` requires unique first keys (solvable with discriminator), 1,000 enum limit accommodates 60 codes easily, 5,000 property limit handles 60 variant schemas. **Main risk:** 5-level nesting depth may be tight. Schema compilation time and output quality with very large schemas are concerns. |
| **Claude** | **No** (for strict mode) | `oneOf` not supported; `anyOf` has severe grammar complexity limits; 60 variants with different property sets would almost certainly exceed the compiled grammar size limit. Would need to fall back to prompt-based "soft" schema enforcement or split into multiple calls. |
| **Gemini** | **No** | ~120 enum limit might technically accommodate 60 codes with short names, but the combined schema complexity (60 `anyOf` variants) would far exceed the "too many states" limit. |

### 2.3 Practical Recommendation

**No provider reliably supports the full discriminated-union-over-60-codes pattern in a single strict-mode call.** Even OpenAI, which comes closest, would likely suffer quality degradation with a schema that complex.

**The proven pattern is a two-phase approach:**

1. **Phase 1 -- Classify:** Use structured output with a simple schema: `[{entry_text, taxonomy_code (enum of 60 values), confidence}]`. This works within all providers' enum limits (OpenAI: 1,000; Gemini: marginal at 120; Claude: likely fine with short code strings).

2. **Phase 2 -- Extract:** For each classified entry (or batch by code), use a code-specific extraction schema. This keeps each schema simple and well within all providers' limits.

This is essentially what CViche already does with Stage 3a/3b (classification) followed by Stage 4 (field extraction).

---

## 3. Variable-Length Arrays and Output Truncation

A CV might have 5 publications or 200. How do structured outputs handle this?

### 3.1 The Core Problem

Structured output schemas can specify arrays, but **cannot enforce minimum or maximum array lengths** (these validation keywords are ignored by the grammar engines). The real constraint is **output token limits**.

### 3.2 Token Limits and Truncation Behavior

| Provider | Max Output Tokens | What Happens on Truncation |
|---|---|---|
| **OpenAI GPT-5.4** | 128,000 | `finish_reason: "length"` -- **incomplete/invalid JSON** is returned |
| **Claude 4.5** | 64,000 | Similar truncation; response may be incomplete |
| **Gemini 3 Pro** | 65,536 | Truncation with incomplete output |

**Key point:** When the output is truncated due to token limits, structured output guarantees are **violated** -- you get invalid JSON. You must check `finish_reason` / `stop_reason` and handle this case.

### 3.3 Practical Impact for CViche

A typical CV entry in JSON (with taxonomy code, extracted fields) runs ~200-500 tokens. So:
- 50 entries: ~10,000-25,000 tokens -- safe on all providers
- 200 entries: ~40,000-100,000 tokens -- safe on OpenAI GPT-5; risky on Claude/Gemini
- 500 entries: ~100,000-250,000 tokens -- risky even on GPT-5

**Recommendation:** Always process in chunks (sections or batches of entries) rather than attempting to extract the entire CV in one call. This is especially important for CVs with extensive publication lists.

**Source:** [Humanloop - Structured Outputs Guide](https://humanloop.com/blog/structured-outputs), [OpenAI Community - Truncation](https://community.openai.com/t/structured-output-issue-in-gpt-4o-api-response-truncation-at-specific-index/1146715)

---

## 4. Quality Degradation: Structured vs. Free-Form

### 4.1 Research Evidence

Multiple 2025 studies have investigated whether constraining output format hurts content quality:

**"The Hidden Cost of Structure" (Schall & de Melo, RANLP 2025):**
- Constrained decoding creates a "fundamental divergence" between base and instruction-tuned models
- Instruction-tuned models may see **reduced** structured output capabilities compared to base models
- Constrained models show steeper gains from few-shot examples (need more demonstrations)
- Task-dependent and model-dependent -- no universal answer

**StructEval Benchmark (Tiger AI Lab, 2025):**
- Even GPT-4o achieves only **76% average** on structured generation tasks
- Open-source models lag at ~67%
- Visual rendering and format conversion tasks are especially hard
- Public benchmark ground truths are themselves error-prone, making evaluation difficult

**BAML Blog -- "Structured Outputs Create False Confidence" (BoundaryML, 2025):**
- Structured outputs guarantee format but not accuracy
- "Perfectly formatted incorrect answers" are a real risk
- BAML's post-generation parsing approach claims 2-4x faster performance than constrained decoding with better accuracy on function-calling benchmarks

**Source:** [RANLP 2025 Paper](https://aclanthology.org/2025.ranlp-1.124/), [StructEval](https://tiger-ai-lab.github.io/StructEval/), [BAML Blog](https://boundaryml.com/blog/structured-outputs-create-false-confidence)

### 4.2 Practical Implications for CViche

- **Classification accuracy** (choosing the right taxonomy code) is unlikely to be hurt by structured output constraints -- enums are a natural fit
- **Extraction quality** (pulling correct text for fields like "journal name" or "grant amount") could potentially degrade with very complex schemas, but simple per-code schemas should be fine
- **Segmentation** (identifying section boundaries) is more of a reasoning task where structured output constraints add modest overhead but don't fundamentally change the task

**The biggest risk is false confidence:** a structured output guarantees the JSON is valid, but the taxonomy code might be wrong or a field might contain hallucinated content. Post-classification validators (which CViche already implements via `core/validators/`) remain essential.

---

## 5. Tool Use / Function Calling for Classification

### 5.1 Tool Use for Taxonomy Lookup

**Can a model use tool calls to look up taxonomy definitions mid-classification?**

Yes, in theory. All three providers support multi-turn tool use where the model can:
1. Receive the CV text
2. Call a `lookup_taxonomy_code` tool with a candidate code to get its definition
3. Use the returned definition to make a classification decision
4. Return the final structured result

**However, this approach has practical problems:**
- **Latency:** Each tool call adds a round trip. For a CV with 100+ entries, this could mean hundreds of tool calls, each taking 1-3 seconds.
- **Context pollution:** Each tool call/response pair consumes context window tokens
- **Reliability:** Research shows function calling reliability degrades with JSON response lengths averaging 24,000-74,000 characters (ComplexFuncBench, 2025)
- **Cost:** Each tool call round-trip incurs input+output token costs

**Source:** [ComplexFuncBench](https://arxiv.org/html/2510.15955v1), [ToolACE - ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/663865ea167425c6c562cb0b6bcf76c7-Paper-Conference.pdf)

### 5.2 Better Alternative: Context Injection

Rather than tool use, the proven approach (which CViche already uses) is to inject the relevant taxonomy codes and their definitions directly into the prompt. With 60 codes, the taxonomy reference adds ~2,000-4,000 tokens to the prompt -- trivial relative to modern context windows.

### 5.3 Tool Use + Structured Output Interaction

**OpenAI:** Function calling supports `strict: true` mode, which applies the same CFG-constrained decoding to tool call arguments. You can have structured output on the final response AND strict tool schemas simultaneously.

**Claude:** Strict tool use validates tool parameters. JSON outputs and strict tool use can be combined -- Claude can call tools with guaranteed-valid parameters AND return structured JSON responses.

**Gemini 3:** Can combine structured outputs with built-in tools (Google Search grounding, URL Context, Code Execution, Function Calling).

---

## 6. Model Comparison for Long-Document Structured Extraction

### 6.1 Head-to-Head (as of March 2026)

| Capability | OpenAI GPT-5.x | Claude 4.5/4.6 | Gemini 3 Pro |
|---|---|---|---|
| **Context window** | 400k | 1M (beta >200k) | 1M |
| **Max output tokens** | 128k | 64k | 65k |
| **Structured output maturity** | Most mature, largest limits | GA but grammar complexity limits | anyOf/recursive support added Nov 2025 |
| **Enum limit** | 1,000 | Grammar-size dependent | ~120 |
| **anyOf / discriminated union** | Yes (with unique first keys) | Limited (no top-level oneOf) | Yes (Nov 2025+) |
| **Properties limit** | 5,000 | Grammar-size dependent | Schema complexity dependent |
| **Nesting limit** | 5 levels | ~3 recommended | Complexity dependent |
| **Lark/regex grammar** | Yes (GPT-5+) | No | No |
| **Long-doc recall** | 98% at 256k (GPT-5.2) | Excellent at 1M | 99.7% at 1M |
| **Cost efficiency** | Moderate | Higher (Opus expensive) | Most cost-efficient |

### 6.2 Benchmarks Relevant to Document Extraction

- **Box.com testing (2025):** GPT-5.2 improved complex extraction accuracy from 59% to 70% -- a significant jump, with best results on visual/document extraction.
- **Gemini 2.5 Pro:** 100% recall up to 530k tokens; 99.7% at 1M tokens (Google's NIAH benchmark).
- **SWE-bench:** Claude 4.5 Sonnet leads at 77.2% -- relevant for code but indicative of complex reasoning ability.
- **No dedicated benchmark** exists specifically for "CV section segmentation + taxonomy classification" across providers.

**Source:** [LM Council Benchmarks](https://lmcouncil.ai/benchmarks), [LLM Stats - GPT-5.2](https://llm-stats.com/models/gpt-5.2-2025-12-11), [Gemini Long Context Docs](https://ai.google.dev/gemini-api/docs/long-context)

### 6.3 Winner for CViche's Use Case

**OpenAI GPT-5.x is the best fit** for structured extraction in CViche, specifically because:
1. **128k output tokens** provides headroom for large CVs with many entries
2. **1,000 enum values** easily accommodates the 60-code taxonomy with room to grow
3. **5,000 properties** handles complex per-code extraction schemas
4. **`anyOf` with discriminators** enables polymorphic schemas (though the full 60-variant schema is still risky)
5. **Most mature structured output implementation** with the best-documented limits

**Claude is the best reasoning engine** and excels at the classification _decision_ itself, but its structured output grammar limits make it poorly suited for complex schema enforcement. Claude is better used with prompt-based JSON (which it follows very reliably even without strict mode) or with simple schemas.

**Gemini is cost-efficient and strong on long context** but the ~120 enum limit is a hard blocker for the 60-code taxonomy with any schema complexity.

---

## 7. Recommendations for CViche Pipeline

### 7.1 Keep the Multi-Stage Architecture

The current 12-stage pipeline with separate segmentation, classification, and extraction stages is **architecturally correct** for the current state of structured output technology. No provider supports the monolithic "segment + classify + extract" pattern reliably.

### 7.2 Adopt Structured Outputs Where They Fit

| Stage | Structured Output Recommendation |
|---|---|
| **1a: Segmentation** | Moderate schema complexity. Could benefit from structured output to guarantee valid hierarchy JSON. Use OpenAI strict mode with a flat schema for segments/headers. |
| **3a: Header Taxonomy Mapping** | Good fit. Simple schema: `{header_text, taxonomy_codes: [enum of 60 codes], confidence}`. OpenAI handles this easily. |
| **3b: Entry Classification** | Good fit. Schema: array of `{entry_id, taxonomy_code (enum), confidence, reasoning}`. The 60-code enum is well within OpenAI's 1,000 limit. |
| **4: Field Extraction** | Best fit. Per-code extraction schemas are simple and well-bounded. Use strict mode per code or per code group. |
| **5c/5d: Formatting** | Low value for strict mode -- these are text generation tasks where format is flexible. |

### 7.3 Use the Instructor Library for Provider Abstraction

The [Instructor library](https://python.useinstructor.com/) (3M+ monthly downloads) provides:
- Define schemas once in Pydantic, use across OpenAI/Claude/Gemini
- Automatic retry on validation failure (re-sends error to model)
- Provider switching with zero code changes
- Handles schema transformation for provider-specific quirks

### 7.4 Design Schemas for Minimum Complexity

Based on all providers' limits:
- **Flatten structures** -- avoid nesting beyond 3 levels
- **Use short enum values** -- the taxonomy codes (`S1`, `M2A`, etc.) are already short, which is ideal
- **Make fields required** with null unions for optional -- reduces grammar state space on Claude
- **Avoid discriminated unions** in a single schema -- use the two-phase classify-then-extract pattern
- **Keep schemas under 100 total properties** for maximum cross-provider compatibility

### 7.5 Handle Truncation Explicitly

Always check `finish_reason` / `stop_reason`. If output was truncated:
1. Retry with a smaller batch of entries
2. Increase `max_output_tokens` if below provider maximum
3. Split the input into smaller chunks

### 7.6 Maintain Post-Classification Validators

Structured output guarantees format, not accuracy. The existing validator pipeline in `core/validators/` (structural header corrections, committee position correction, reasoning consistency, grant status, etc.) remains essential. Structured outputs reduce parsing errors but do not reduce classification errors.

---

## 8. Cross-Provider Comparison Table

| Feature | OpenAI GPT-5.x | Claude 4.5+ | Gemini 3 |
|---|---|---|---|
| **Guaranteed schema conformance** | Yes (strict mode) | Yes (strict mode) | Yes (strict mode) |
| **Max enum values** | 1,000 | Grammar-dependent (~hundreds) | ~120 |
| **Max properties** | 5,000 | Grammar-dependent | Complexity-dependent |
| **Max nesting** | 5 levels | ~3 recommended | Complexity-dependent |
| **anyOf support** | Yes (unique first keys) | Limited (no top-level oneOf) | Yes (Nov 2025+) |
| **$ref / recursive** | Yes | No ($ref unsupported) | Yes (Nov 2025+) |
| **Optional fields** | Via null union | Via null union (doubles grammar) | Yes |
| **Max output tokens** | 128,000 | 64,000 | 65,536 |
| **Output truncation handling** | finish_reason: "length" | stop_reason check | Incomplete flag |
| **Schema compilation caching** | First-call overhead | 24-hour cache | Not documented |
| **Validation keywords (min/max/pattern)** | Ignored (not enforced) | Limited support | Some (Nov 2025+) |
| **Lark/regex grammar** | Yes (GPT-5+) | No | No |
| **Tool use + structured output** | Yes (both strict) | Yes (both strict) | Yes (Gemini 3+) |

---

## Sources

### OpenAI
- [Structured Model Outputs - Official Docs](https://platform.openai.com/docs/guides/structured-outputs)
- [Introducing Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- [Structured Outputs Limits Raised](https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593)
- [Structured Outputs Deep-dive](https://community.openai.com/t/structured-outputs-deep-dive/930169)
- [anyOf First Key Uniqueness Issue](https://community.openai.com/t/objects-provided-via-anyof-must-not-share-identical-first-keys-error-in-structured-output/958572)
- [GPT-5 Model Docs](https://platform.openai.com/docs/models/gpt-5)
- [GPT-5.4 Model Docs](https://developers.openai.com/api/docs/models/gpt-5.4)
- [GPT-5 New Params and Tools Cookbook](https://cookbook.openai.com/examples/gpt-5/gpt-5_new_params_and_tools)
- [Best Practices for Structured Extraction - Azure](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/best-practices-for-structured-extraction-from-documents-using-azure-openai/4397282)

### Anthropic
- [Claude Structured Outputs - Official Docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Structured Outputs Blog](https://claude.com/blog/structured-outputs-on-the-claude-developer-platform)
- [Grammar Too Large Issue #1185](https://github.com/anthropics/anthropic-sdk-python/issues/1185)
- [Hands-On Guide - Towards Data Science](https://towardsdatascience.com/hands-on-with-anthropics-new-structured-output-capabilities/)
- [Hacker News Discussion](https://news.ycombinator.com/item?id=45930598)

### Google
- [Gemini Structured Output Docs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Improving Structured Outputs Blog](https://blog.google/technology/developers/gemini-api-structured-outputs/)
- [Enum Limit Discussion](https://discuss.ai.google.dev/t/max-number-of-enums-in-response-schema-for-structured-output/40860)
- [Large Enum Support Request #950](https://github.com/googleapis/python-genai/issues/950)
- [Too Many States Error #660](https://github.com/googleapis/python-genai/issues/660)

### Research Papers & Analysis
- [The Hidden Cost of Structure - RANLP 2025](https://aclanthology.org/2025.ranlp-1.124/)
- [StructEval Benchmark](https://tiger-ai-lab.github.io/StructEval/)
- [Structured Outputs Create False Confidence - BAML](https://boundaryml.com/blog/structured-outputs-create-false-confidence)
- [LLM Structured Output Benchmarks Mistakes - Cleanlab](https://cleanlab.ai/blog/structured-output-benchmark/)
- [Computational Complexity of Schema-Guided Extraction - Pulse AI](https://www.runpulse.com/blog/computational-complexity-of-schema)
- [JSONSchemaBench](https://arxiv.org/html/2501.10868v1)
- [ToolACE - ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/663865ea167425c6c562cb0b6bcf76c7-Paper-Conference.pdf)

### Libraries & Frameworks
- [Instructor Library](https://python.useinstructor.com/)
- [BAML - BoundaryML](https://boundaryml.com/)
- [LangExtract - Google](https://github.com/google/langextract)
- [Every Way to Get Structured Output - BAML Blog](https://boundaryml.com/blog/structured-output-from-llms)
- [The Guide to Structured Outputs - Agenta](https://agenta.ai/blog/the-guide-to-structured-outputs-and-function-calling-with-llms)
- [Structured Outputs Guide - Humanloop](https://humanloop.com/blog/structured-outputs)

### Benchmarks & Comparisons
- [LM Council Benchmarks Mar 2026](https://lmcouncil.ai/benchmarks)
- [LLM Stats - GPT-5.2](https://llm-stats.com/models/gpt-5.2-2025-12-11)
- [2025 LLM Review](https://atoms.dev/blog/2025-llm-review-gpt-5-2-gemini-3-pro-claude-4-5)
