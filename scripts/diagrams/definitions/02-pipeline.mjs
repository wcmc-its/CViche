/**
 * View 2 — The 12-stage CV processing pipeline. What each stage does, which call an
 * LLM (AWS Bedrock Claude), which call an external API, and which are deterministic.
 *
 * LLM usage is taken from the stage modules themselves (each `call_llm(...)` site),
 * NOT the step_registry uses_llm flags, which are stale (e.g. they still mark stage 2
 * as non-LLM; 5b's flag was fixed by #523). Ten of the twelve stages call an LLM; only 1b (index
 * mapping) and 5 (PubMed) do not. Stage 6's headline job is deterministic Word
 * rendering, but it also calls the LLM to classify presentation/service geo-scope.
 *
 * Source: src/unified_pipeline/stage_*.py (the call_llm sites) ·
 *   src/unified_pipeline/config/llm_config.yaml (models) ·
 *   web_interface/backend/app/pipeline/orchestrator.py (dispatch) · README.md "Pipeline Stages"
 */
import { A } from "../lib.mjs";

const LLM = { tone: "live", text: "LLM" };
const API = { tone: "planned", text: "API" };
const DET = { tone: "ondemand", text: "no LLM" };

const nodes = {
  input: { x: 24, y: 316, w: 158, h: 80, kind: "data", title: "CV input",
           sub: ["Word .docx (or .pdf)"] },

  s1a: { x: 208, y: 210, w: 180, h: 80, kind: "app", title: "1a · Segment", chip: LLM,
         sub: ["section headers + structure"] },
  s1b: { x: 208, y: 316, w: 180, h: 80, kind: "app", title: "1b · Mapping", chip: DET,
         sub: ["headers → para indices"] },
  s2:  { x: 208, y: 422, w: 180, h: 80, kind: "app", title: "2 · Extract", chip: LLM,
         sub: ["entries + full text / section"] },

  s3a: { x: 428, y: 210, w: 180, h: 80, kind: "app", title: "3a · Header map", chip: LLM,
         sub: ["headers → WCM codes"] },
  s3b: { x: 428, y: 316, w: 180, h: 80, kind: "app", title: "3b · Classify", chip: LLM,
         sub: ["entries → codes", "→ Claude Haiku 4.5"] },

  s4:  { x: 648, y: 210, w: 180, h: 80, kind: "app", title: "4 · Fields", chip: LLM,
         sub: ["authors · DOIs · grants"] },
  s45: { x: 648, y: 316, w: 180, h: 80, kind: "app", title: "4.5 · Summary", chip: LLM,
         sub: ["M1 biosketch narrative"] },

  s5:  { x: 868, y: 210, w: 180, h: 80, kind: "app", title: "5 · PubMed", chip: API,
         sub: ["MeSH · abstracts · PMCID"] },
  s5b: { x: 868, y: 316, w: 180, h: 80, kind: "app", title: "5b · Locations", chip: LLM,
         sub: ["institution city/state (LLM)"] },

  s5c: { x: 1088, y: 210, w: 180, h: 80, kind: "app", title: "5c · Teaching", chip: LLM,
         sub: ["K-codes → readable"] },
  s5d: { x: 1088, y: 316, w: 180, h: 80, kind: "app", title: "5d · Citations", chip: LLM,
         sub: ["→ Vancouver style"] },

  s6:  { x: 1308, y: 316, w: 180, h: 80, kind: "app", title: "6 · WCM Word", chip: LLM,
         sub: ["fills WCM .docx by code", "+ geo-scope classify (LLM)"] },

  output: { x: 1512, y: 312, w: 176, h: 88, kind: "data", title: "Standardized CV",
            sub: ["WCM .docx", "primary output"] },

  llm: { x: 196, y: 72, w: 1072, h: 80, kind: "aws", title: "AWS Bedrock — Claude",
         sub: ["Sonnet 4.6 — default (all LLM stages)", "Haiku 4.5 — stage 3b only",
               "if llm_config.yaml is missing: openai / gpt-4o-mini"] },

  pubmed: { x: 883, y: 556, w: 180, h: 56, kind: "ext", title: "NCBI PubMed",
            sub: ["E-utilities"] },
};

const groups = [
  { x: 194, y: 185, w: 208, h: 340, kind: "app", title: "Segment" },
  { x: 414, y: 185, w: 208, h: 340, kind: "app", title: "Classify" },
  { x: 634, y: 185, w: 208, h: 340, kind: "app", title: "Extract" },
  { x: 854, y: 185, w: 208, h: 340, kind: "app", title: "Enrich" },
  { x: 1074, y: 185, w: 208, h: 340, kind: "app", title: "Format" },
  { x: 1294, y: 185, w: 208, h: 340, kind: "app", title: "Render" },
];

const flow = (a, b, label) => ({ p0: A(a, "r"), p1: A(b, "l"), color: "green", label });
const down = (a, b, label) => ({ p0: A(a, "b"), p1: A(b, "t"), color: "green", label });

const edges = [
  { p0: A(nodes.input, "r"), p1: A(nodes.s1a, "l"), color: "green", label: "ingest" },
  down(nodes.s1a, nodes.s1b),
  down(nodes.s1b, nodes.s2),
  flow(nodes.s2, nodes.s3a),
  down(nodes.s3a, nodes.s3b),
  flow(nodes.s3b, nodes.s4),
  down(nodes.s4, nodes.s45),
  flow(nodes.s45, nodes.s5),
  down(nodes.s5, nodes.s5b),
  flow(nodes.s5b, nodes.s5c),
  down(nodes.s5c, nodes.s5d),
  flow(nodes.s5d, nodes.s6),
  { p0: A(nodes.s6, "r"), p1: A(nodes.output, "l"), color: "green", label: "render" },

  // LLM dependency: representative arrows from the model lane (per-stage truth is in the chips).
  { p0: A(nodes.llm, "b", 0.10), p1: A(nodes.s1a, "t"), color: "violet", dash: true },
  { p0: A(nodes.llm, "b", 0.31), p1: A(nodes.s3a, "t"), color: "violet", dash: true },
  { p0: A(nodes.llm, "b", 0.515), p1: A(nodes.s4, "t"), color: "violet", dash: true },
  { p0: A(nodes.llm, "b", 0.925), p1: A(nodes.s5c, "t"), color: "violet", dash: true, label: "Claude" },

  // External enrichment API (PubMed only; institution lookup in 5b is an LLM call).
  { p0: A(nodes.s5, "l"), p1: A(nodes.pubmed, "t", 0.5), color: "teal",
    points: [{ x: 843, y: 300 }, { x: 843, y: 520 }], label: "PubMed" },
];

export const spec = { id: "pipeline", vb: [1710, 660], groups, nodes, edges };

export const meta = {
  nav: "② Pipeline",
  kicker: "View 2 · processing pipeline",
  heading: "The 12-stage CV pipeline",
  dot: "#0ca678",
  blurb:
    "Each stage produces JSON the next stage consumes, grouped into six phases. <b>Ten of the " +
    "twelve stages call an LLM</b> (AWS Bedrock Claude — Sonnet 4.6 by default; stage 3b runs the " +
    "cheaper, gold-eval-preferred <b>Haiku 4.5</b>). The two exceptions: <b>1b</b> (header→index " +
    "mapping) is deterministic, and <b>5</b> (PubMed enrichment) calls an external API. Stage 6's " +
    "headline job is rendering the WCM Word document — the pipeline's primary output — but it also " +
    "calls the LLM to classify the geographic scope of presentations and service.",
  legend: [
    { fill: "#fff4d6", stroke: "#f08c00", label: "Document in / out" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "Pipeline stage" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "Managed LLM (Bedrock)" },
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "External API" },
  ],
  edgeLegend: [
    { color: "green", label: "stage → stage (JSON)" },
    { color: "violet", dash: true, label: "LLM inference" },
    { color: "teal", label: "external API call" },
  ],
  footnote:
    "Chips: <b>LLM</b> = Bedrock Claude call · <b>API</b> = external lookup (NCBI PubMed) · " +
    "<b>no&nbsp;LLM</b> = deterministic. Chips come from each stage module's <code>call_llm</code> " +
    "sites, not the (stale) step_registry flags. The CLI and the web app run this same sequence.",
  seeAlso: [
    { id: "context", label: "① system context" },
    { id: "runtime", label: "③ web app & run lifecycle" },
  ],
  source:
    "src/unified_pipeline/stage_*.py (call_llm sites) · src/unified_pipeline/config/llm_config.yaml · " +
    "web_interface/backend/app/pipeline/orchestrator.py",
};
