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
 * Stage metadata (id, display name, grid position, LLM/API/deterministic classification,
 * model override, node sub-lines) lives ONCE, in the `STAGES` array below — it used to be
 * duplicated by hand across `nodes`, `meta.blurb`, `meta.footnote`, and `nodes.llm.sub`,
 * which let the code and the diagram's labels drift out of sync. `nodes` and the per-stage
 * bits of `meta.blurb`/`meta.footnote`/`nodes.llm.sub` are now derived from it (PR #642
 * review item 1). Node/group coordinates are likewise derived from each stage's column/row
 * index against named grid constants rather than typed as literal positions (item 8), and
 * the LLM-fan-out edges' fractional anchor points are named constants instead of bare
 * numbers (item 9).
 *
 * Source: src/unified_pipeline/stage_*.py (the call_llm sites) ·
 *   src/unified_pipeline/config/llm_config.yaml (models) ·
 *   web_interface/backend/app/pipeline/orchestrator.py (dispatch) · README.md "Pipeline Stages"
 */
import { A } from "../lib.mjs";

const LLM = { tone: "live", text: "LLM", desc: "Bedrock Claude call" };
const API = { tone: "planned", text: "API", desc: "external lookup (NCBI PubMed)" };
const DET = { tone: "ondemand", text: "no LLM", desc: "deterministic" };

// Model display names, single-sourced so "Sonnet 4.6" / "Haiku 4.5" aren't retyped at each
// of their four call sites (a stage's own sub-line, the AWS Bedrock node's sub-lines ×2, and
// meta.blurb) — PR #642 review item 1.
const MODEL_DEFAULT = "Sonnet 4.6";
const MODEL_OVERRIDE = "Haiku 4.5";

// --- Layout grid -----------------------------------------------------------------------
// The 12 stage boxes sit on a regular column/row grid; column = processing phase, row =
// position within that phase's band (top/mid/bottom). Named here and derived from each
// stage's (col, row) below instead of typed as 12 pairs of literal x/y — PR #642 review
// item 8. Phase bands (the labelled rectangles behind each column) derive their x/width
// from the same COL_STEP/GROUP_PAD_X so a band always frames its column's boxes exactly.
const STAGE_X0 = 208;      // column 0's left edge
const STAGE_Y0 = 210;      // row 0's top edge
const COL_STEP = 220;      // x distance between adjacent columns (box width 180 + 40 gutter)
const ROW_STEP = 106;      // y distance between adjacent rows (box height 80 + 26 gutter)
const STAGE_W = 180;
const STAGE_H = 80;
const GROUP_PAD_X = 14;              // phase band's horizontal margin around its column's boxes
const PHASE_BAND_Y = 185;            // fixed top for every band, regardless of column row count
const PHASE_BAND_H = 340;            // fixed height so all six bands read as one uniform row

// One phase name per column (left → right); the band title, not any one stage's own name.
const PHASES = ["Segment", "Classify", "Extract", "Enrich", "Format", "Render"];

// --- Stage metadata: the single source of truth -----------------------------------------
// key  = the `nodes` property this stage renders as (kept stable for the edges below).
// id   = the stage number shown on the box ("3b", "4.5", …) and referenced in prose.
// name = the short phase-step label shown after "id · " on the box.
// col/row = position in the layout grid above.
// usesLlm/api = LLM vs. external-API vs. deterministic chip (DET is the fallback).
// model = "haiku" marks the one stage 3b cost/accuracy override; everything else is
//   MODEL_DEFAULT (Sonnet). note = the short parenthetical used when a stage is named in
//   meta.blurb (only the two non-LLM stages are called out there today).
const STAGES = [
  { key: "s1a", id: "1a", name: "Segment", col: 0, row: 0, usesLlm: true, model: "default",
    sub: ["section headers + structure"] },
  { key: "s1b", id: "1b", name: "Mapping", col: 0, row: 1, usesLlm: false,
    note: "header→index mapping", sub: ["headers → para indices"] },
  { key: "s2", id: "2", name: "Extract", col: 0, row: 2, usesLlm: true, model: "default",
    sub: ["entries + full text / section"] },

  { key: "s3a", id: "3a", name: "Header map", col: 1, row: 0, usesLlm: true, model: "default",
    sub: ["headers → WCM codes"] },
  { key: "s3b", id: "3b", name: "Classify", col: 1, row: 1, usesLlm: true, model: "haiku",
    sub: ["entries → codes", `→ Claude ${MODEL_OVERRIDE}`] },

  { key: "s4", id: "4", name: "Fields", col: 2, row: 0, usesLlm: true, model: "default",
    sub: ["authors · DOIs · grants"] },
  { key: "s45", id: "4.5", name: "Summary", col: 2, row: 1, usesLlm: true, model: "default",
    sub: ["M1 biosketch narrative"] },

  { key: "s5", id: "5", name: "PubMed", col: 3, row: 0, usesLlm: false, api: true,
    note: "PubMed enrichment", sub: ["MeSH · abstracts · PMCID"] },
  { key: "s5b", id: "5b", name: "Locations", col: 3, row: 1, usesLlm: true, model: "default",
    sub: ["institution city/state (LLM)"] },

  { key: "s5c", id: "5c", name: "Teaching", col: 4, row: 0, usesLlm: true, model: "default",
    sub: ["K-codes → readable"] },
  { key: "s5d", id: "5d", name: "Citations", col: 4, row: 1, usesLlm: true, model: "default",
    sub: ["→ Vancouver style"] },

  { key: "s6", id: "6", name: "WCM Word", col: 5, row: 1, usesLlm: true, model: "default",
    sub: ["fills WCM .docx by code", "+ geo-scope classify (LLM)"] },
];

const LLM_STAGES = STAGES.filter((s) => s.usesLlm);
const NON_LLM_STAGES = STAGES.filter((s) => !s.usesLlm);
const HAIKU_STAGE = STAGES.find((s) => s.model === "haiku");

// Guardrail, not just a comment: meta.blurb below hand-states "Ten of the twelve" and names
// the two exceptions by id/note. English prose isn't worth auto-generating from a number
// (there's no clean way to derive the word "Ten" from STAGES.length), so instead this makes
// any future drift LOUD — the build throws — rather than silently stale. PR #642 review item 1.
if (STAGES.length !== 12 || LLM_STAGES.length !== 10 ||
    NON_LLM_STAGES.map((s) => s.id).join(",") !== "1b,5" || !HAIKU_STAGE) {
  throw new Error(
    `02-pipeline.mjs: STAGES drifted (total=${STAGES.length}, llm=${LLM_STAGES.length}, ` +
    `non-llm=${NON_LLM_STAGES.map((s) => s.id).join(",") || "none"}, ` +
    `haiku-override=${HAIKU_STAGE ? HAIKU_STAGE.id : "none"}) — update the "Ten of the twelve" ` +
    `and "1b"/"5" wording hand-written into meta.blurb before shipping.`
  );
}
const [S1B, S5] = NON_LLM_STAGES; // order asserted above: header-mapping, then PubMed

const nodes = {
  input: { x: 24, y: 316, w: 158, h: 80, kind: "data", title: "CV input",
           sub: ["Word .docx (or .pdf)"] },

  ...Object.fromEntries(STAGES.map((s) => [s.key, {
    x: STAGE_X0 + s.col * COL_STEP,
    y: STAGE_Y0 + s.row * ROW_STEP,
    w: STAGE_W, h: STAGE_H,
    kind: "app",
    title: `${s.id} · ${s.name}`,
    chip: s.api ? API : s.usesLlm ? LLM : DET,
    sub: s.sub,
  }])),

  output: { x: 1512, y: 312, w: 176, h: 88, kind: "data", title: "Standardized CV",
            sub: ["WCM .docx", "primary output"] },

  llm: { x: 196, y: 72, w: 1072, h: 80, kind: "aws", title: "AWS Bedrock — Claude",
         sub: [`${MODEL_DEFAULT} — default (all LLM stages)`,
               `${MODEL_OVERRIDE} — stage ${HAIKU_STAGE.id} only`,
               "if llm_config.yaml is missing: openai / gpt-4o-mini"] },

  pubmed: { x: 883, y: 556, w: 180, h: 56, kind: "ext", title: "NCBI PubMed",
            sub: ["E-utilities"] },
};

const groups = PHASES.map((title, col) => ({
  x: STAGE_X0 - GROUP_PAD_X + col * COL_STEP,
  y: PHASE_BAND_Y,
  w: STAGE_W + 2 * GROUP_PAD_X,
  h: PHASE_BAND_H,
  kind: "app",
  title,
}));

const flow = (a, b, label) => ({ p0: A(a, "r"), p1: A(b, "l"), color: "green", label });
const down = (a, b, label) => ({ p0: A(a, "b"), p1: A(b, "t"), color: "green", label });

// Fractional taps along the AWS Bedrock band's bottom edge (`A(nodes.llm, "b", f)`), one per
// representative dashed "LLM inference" arrow drawn down into the pipeline below — a sample,
// not an exhaustive wire-up (per-stage truth is each stage's own chip). Each is hand-nudged a
// few percent right of its target stage's horizontal center so the dashed line clears the
// phase-band title tab above the box; that hand-tuning is why these are named constants
// rather than derived from the target nodes' geometry (deriving them would move the lines a
// few px and change the rendered diagram) — PR #642 review item 9. 0.925 in particular sits
// short of the band's right edge (1.0) on purpose: stage 6 has no dashed arrow drawn to it
// here even though it does call an LLM (its geo-scope classification is called out in prose
// instead, in meta.blurb/meta.footnote).
const LLM_FANOUT_TO_S1A = 0.10;
const LLM_FANOUT_TO_S3A = 0.31;
const LLM_FANOUT_TO_S4 = 0.515;
const LLM_FANOUT_TO_S5C = 0.925;

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
  { p0: A(nodes.llm, "b", LLM_FANOUT_TO_S1A), p1: A(nodes.s1a, "t"), color: "violet", dash: true },
  { p0: A(nodes.llm, "b", LLM_FANOUT_TO_S3A), p1: A(nodes.s3a, "t"), color: "violet", dash: true },
  { p0: A(nodes.llm, "b", LLM_FANOUT_TO_S4), p1: A(nodes.s4, "t"), color: "violet", dash: true },
  { p0: A(nodes.llm, "b", LLM_FANOUT_TO_S5C), p1: A(nodes.s5c, "t"), color: "violet", dash: true, label: "Claude" },

  // External enrichment API (PubMed only; institution lookup in 5b is an LLM call).
  { p0: A(nodes.s5, "l"), p1: A(nodes.pubmed, "t", 0.5), color: "teal",
    points: [{ x: 843, y: 300 }, { x: 843, y: 520 }], label: "PubMed" },
];

export const spec = { id: "pipeline", vb: [1710, 660], groups, nodes, edges };

// Chip legend text (footnote below), derived from the three chip definitions above rather
// than retyped — PR #642 review item 1.
const CHIP_LEGEND_TEXT = [LLM, API, DET]
  .map((c) => `<b>${c.text.replace(" ", "&nbsp;")}</b> = ${c.desc}`)
  .join(" · ");

export const meta = {
  nav: "② Pipeline",
  kicker: "View 2 · processing pipeline",
  heading: "The 12-stage CV pipeline",
  dot: "#0ca678",
  blurb:
    "Each stage produces JSON the next stage consumes, grouped into six phases. <b>Ten of the " +
    "twelve stages call an LLM</b> (AWS Bedrock Claude — " + MODEL_DEFAULT + " by default; stage " +
    HAIKU_STAGE.id + " runs the cheaper, gold-eval-preferred <b>" + MODEL_OVERRIDE + "</b>). The two " +
    "exceptions: <b>" + S1B.id + "</b> (" + S1B.note + ") is deterministic, and <b>" + S5.id + "</b> (" +
    S5.note + ") calls an external API. Stage 6's headline job is rendering the WCM Word document " +
    "— the pipeline's primary output — but it also calls the LLM to classify the geographic scope " +
    "of presentations and service.",
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
    `Chips: ${CHIP_LEGEND_TEXT}. Chips come from each stage module's <code>call_llm</code> sites, ` +
    "not the (stale) step_registry flags. The CLI and the web app run this same sequence.",
  seeAlso: [
    { id: "context", label: "① system context" },
    { id: "runtime", label: "③ web app & run lifecycle" },
  ],
  source:
    "src/unified_pipeline/stage_*.py (call_llm sites) · src/unified_pipeline/config/llm_config.yaml · " +
    "web_interface/backend/app/pipeline/orchestrator.py",
};
