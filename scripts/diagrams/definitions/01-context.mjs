/**
 * View 1 — System context. What feeds CViche and who it serves, in one glance.
 *
 * Source: web_interface/backend/app/main.py · src/unified_pipeline/config/llm_config.yaml ·
 *   web_interface/backend/app/pipeline/step_registry.py · src/unified_pipeline/stage_5_pubmed_enrichment.py ·
 *   README.md
 */
import { A } from "../lib.mjs";

const nodes = {
  cli: { x: 40, y: 150, w: 240, h: 86, kind: "ext", title: "CLI user",
         sub: ["run_full_pipeline.py", "batch / single CV"] },
  web: { x: 40, y: 300, w: 240, h: 100, kind: "ext", title: "Web user",
         sub: ["uploads .docx / .pdf", "SAML or email login", "live progress (WebSocket)"] },

  spa: { x: 360, y: 150, w: 360, h: 86, kind: "net", title: "React 18 SPA",
         sub: ["Vite · Tailwind · React Router", "upload · progress · admin"] },
  api: { x: 360, y: 280, w: 360, h: 90, kind: "app", title: "FastAPI backend",
         sub: ["REST /api · WebSocket /ws", "runs · steps · auth · admin"] },
  pipe: { x: 360, y: 414, w: 360, h: 82, kind: "app", title: "12-stage CV pipeline",
          sub: ["src/unified_pipeline · orchestrator"] },

  db:  { x: 840, y: 150, w: 350, h: 64, kind: "data", title: "Run database",
         sub: ["MariaDB (prod) / SQLite (dev)"] },
  llm: { x: 840, y: 236, w: 350, h: 78, kind: "aws", title: "LLM provider",
         sub: ["AWS Bedrock (Claude) — default", "OpenAI — alternative"] },
  pubmed: { x: 840, y: 336, w: 350, h: 58, kind: "ext", title: "NCBI PubMed",
            sub: ["E-utilities — publication metadata"] },
  store: { x: 840, y: 416, w: 350, h: 58, kind: "data", title: "Object storage",
           sub: ["S3 (prod) / local disk (dev)"] },
};

const groups = [
  { x: 20, y: 110, w: 290, h: 320, kind: "ext", title: "Users" },
  { x: 330, y: 110, w: 420, h: 430, kind: "app", title: "CViche" },
  { x: 810, y: 110, w: 400, h: 384, kind: "data", title: "Data & external services" },
];

const edges = [
  { p0: A(nodes.web, "r"), p1: A(nodes.spa, "l", 0.6), color: "indigo", label: "browser (HTTPS)" },
  { p0: A(nodes.spa, "b"), p1: A(nodes.api, "t"), color: "indigo", label: "/api · /ws" },
  { p0: A(nodes.api, "b"), p1: A(nodes.pipe, "t"), color: "green", label: "start run → orchestrate" },
  { p0: A(nodes.cli, "r"), p1: A(nodes.pipe, "l", 0.35), color: "gray", dash: true,
    points: [{ x: 320, y: 193 }, { x: 320, y: 443 }], label: "run_full_pipeline.py" },

  { p0: A(nodes.api, "r", 0.3), p1: A(nodes.db, "l"), color: "amber", label: "run / step state" },
  { p0: A(nodes.pipe, "r", 0.2), p1: A(nodes.llm, "l"), color: "violet", label: "LLM (10 stages)" },
  { p0: A(nodes.pipe, "r", 0.55), p1: A(nodes.pubmed, "l"), color: "teal", label: "stage 5" },
  { p0: A(nodes.pipe, "r", 0.9), p1: A(nodes.store, "l"), color: "amber", label: "artifacts" },
];

export const spec = { id: "context", vb: [1240, 580], groups, nodes, edges };

export const meta = {
  nav: "① Context",
  kicker: "View 1 · system context",
  heading: "System context",
  dot: "#7d1c1c",
  blurb:
    "CViche standardizes academic CVs into the <b>Weill Cornell Medicine</b> format. Two entry " +
    "points drive the same <b>12-stage pipeline</b>: a <b>CLI</b> for batch runs and a <b>React + " +
    "FastAPI</b> web app for interactive use. The pipeline calls an <b>LLM provider</b> (AWS Bedrock " +
    "by default, OpenAI as an alternative) for ten of its twelve stages, enriches publications via " +
    "<b>PubMed</b>, and persists runs and artifacts to a database and object store.",
  legend: [
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "User / external" },
    { fill: "#e7ecff", stroke: "#4263eb", label: "Web tier" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "Compute" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "Managed LLM" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Store" },
  ],
  edgeLegend: [
    { color: "indigo", label: "browser ↔ web tier" },
    { color: "green", label: "orchestrate run" },
    { color: "violet", label: "LLM inference" },
    { color: "teal", label: "external enrichment API" },
    { color: "amber", label: "persisted state / artifacts" },
    { color: "gray", dash: true, label: "CLI (no web app)" },
  ],
  seeAlso: [
    { id: "pipeline", label: "② the 12-stage pipeline" },
    { id: "runtime", label: "③ web app & run lifecycle" },
    { id: "deployment", label: "④ deployment topology" },
  ],
  source:
    "web_interface/backend/app/main.py · src/unified_pipeline/config/llm_config.yaml · " +
    "web_interface/backend/app/pipeline/step_registry.py · README.md",
};
