/**
 * View 3 — Web app & run lifecycle. How a browser drives a run: upload, start
 * (per-pod admission gate), background execution, live progress, and download.
 *
 * Source: web_interface/backend/app/api/upload.py · api/runs.py (start_run, BackgroundTasks) ·
 *   pipeline/concurrency.py · api/websocket.py (/ws/run/{id}/stream) · pipeline/event_emitter.py ·
 *   pipeline/redis_broker.py · storage/factory.py · database.py
 */
import { A } from "../lib.mjs";

const nodes = {
  spa: { x: 40, y: 185, w: 210, h: 190, kind: "net", title: "React SPA (browser)",
         sub: ["① uploads .docx", "② starts the run", "③ polls + WebSocket", "④ downloads .docx"] },

  upload: { x: 300, y: 120, w: 250, h: 74, kind: "app", title: "POST /api/upload",
            sub: ["validate type + size (≤10 MB)", "create run + 12 steps"] },
  start: { x: 300, y: 210, w: 250, h: 72, kind: "app", title: "POST /run/{id}/start",
           sub: ["admission gate → background task"] },
  status: { x: 300, y: 300, w: 250, h: 70, kind: "app", title: "GET /run/{id}/status",
            sub: ["run + 12 step states (cost)"] },
  download: { x: 300, y: 388, w: 250, h: 70, kind: "app", title: "GET /run/{id}/data/{f}",
              sub: ["resolves a download URL"] },

  worker: { x: 610, y: 206, w: 240, h: 120, kind: "app", title: "Background run",
            sub: ["per-pod gate:", "CVICHE_MAX_CONCURRENT_RUNS", "(429 when full)", "asyncio.run(execute)"] },
  orch: { x: 900, y: 206, w: 240, h: 120, kind: "app", title: "PipelineOrchestrator",
          sub: ["runs the 12 stages (view ②)", "writes DB + storage", "emits step events"] },

  events: { x: 610, y: 398, w: 240, h: 92, kind: "net", title: "Live progress",
            sub: ["WebSocket /ws/run/{id}/stream", "+ Redis broker (replicas > 1)"] },

  db: { x: 1180, y: 150, w: 160, h: 84, kind: "data", title: "Run database",
        sub: ["runs · steps", "logs · cost / tokens"] },
  store: { x: 1180, y: 268, w: 160, h: 90, kind: "data", title: "Storage",
           sub: ["uploads + outputs", "S3 / local"] },
};

const groups = [
  { x: 284, y: 96, w: 282, h: 380, kind: "app", title: "FastAPI endpoints" },
  { x: 594, y: 188, w: 556, h: 150, kind: "app", title: "Background asyncio task" },
];

const edges = [
  { p0: A(nodes.spa, "r", 0.18), p1: A(nodes.upload, "l"), color: "green", label: "① upload" },
  { p0: A(nodes.spa, "r", 0.42), p1: A(nodes.start, "l"), color: "green", label: "② start" },
  { p0: A(nodes.spa, "r", 0.66), p1: A(nodes.status, "l"), color: "green", label: "③ poll · 2s" },
  { p0: A(nodes.spa, "r", 0.9), p1: A(nodes.download, "l"), color: "green", label: "④ download" },

  { p0: A(nodes.upload, "r"), p1: A(nodes.db, "l", 0.3), color: "amber",
    points: [{ x: 588, y: 157 }, { x: 1150, y: 157 }], label: "create run + 12 steps" },
  { p0: A(nodes.start, "r"), p1: A(nodes.worker, "l"), color: "indigo", label: "acquire slot" },
  { p0: A(nodes.worker, "r"), p1: A(nodes.orch, "l"), color: "green", label: "execute()" },

  { p0: A(nodes.orch, "r", 0.1), p1: A(nodes.db, "l", 0.8), color: "amber", label: "step state" },
  { p0: A(nodes.orch, "r", 0.8), p1: A(nodes.store, "l", 0.4), color: "amber", label: "artifacts" },
  { p0: A(nodes.orch, "b", 0.2), p1: A(nodes.events, "t", 0.85), color: "violet", label: "step events" },

  { p0: A(nodes.status, "r"), p1: A(nodes.db, "l", 0.7), color: "amber",
    points: [{ x: 588, y: 335 }, { x: 588, y: 170 }, { x: 1150, y: 170 }], label: "read state" },
  { p0: A(nodes.download, "r"), p1: A(nodes.store, "l", 0.85), color: "amber",
    points: [{ x: 582, y: 423 }, { x: 582, y: 366 }, { x: 1150, y: 366 }], label: "presigned URL" },

  { p0: A(nodes.events, "l"), p1: A(nodes.spa, "b", 0.6), color: "violet", dash: true,
    points: [{ x: 560, y: 516 }, { x: 150, y: 516 }], label: "live progress" },
];

export const spec = { id: "runtime", vb: [1360, 560], groups, nodes, edges };

export const meta = {
  nav: "③ Runtime",
  kicker: "View 3 · web app & run lifecycle",
  heading: "Web app & run lifecycle",
  dot: "#4263eb",
  blurb:
    "The browser <b>uploads</b> a CV (creating a run + 12 step rows), then <b>starts</b> it. Start " +
    "passes a <b>per-pod admission gate</b> (<code>CVICHE_MAX_CONCURRENT_RUNS</code>; full → HTTP 429) " +
    "and schedules an <b>in-process asyncio background task</b> that runs the orchestrator — the API " +
    "returns immediately. Progress reaches the browser two ways: it <b>polls status every 2 s</b> and " +
    "subscribes to a <b>WebSocket</b> fed by step events (a Redis broker carries them across pods when " +
    "replicas > 1). When the run finishes, the browser downloads the WCM document.",
  legend: [
    { fill: "#e7ecff", stroke: "#4263eb", label: "Browser / live channel" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "FastAPI / background" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Database / storage" },
  ],
  edgeLegend: [
    { color: "green", label: "request (sync, returns now)" },
    { color: "indigo", label: "admission control" },
    { color: "amber", label: "read / write persistence" },
    { color: "violet", label: "step events" },
    { color: "violet", dash: true, label: "live push to browser" },
  ],
  footnote:
    "The run executes inside the web process (FastAPI <code>BackgroundTasks</code> → " +
    "<code>asyncio.run(orchestrator.execute())</code>), so the admission gate protects the pod's CPU and memory.",
  seeAlso: [
    { id: "pipeline", label: "② what the orchestrator runs" },
    { id: "deployment", label: "④ where it runs" },
  ],
  source:
    "web_interface/backend/app/api/runs.py · pipeline/concurrency.py · api/websocket.py · " +
    "pipeline/event_emitter.py · pipeline/redis_broker.py",
};
