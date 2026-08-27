/**
 * Fact verifier for the CViche architecture diagrams (tier 3 of 4 — see
 * references/PLAYBOOK.md). Asserts that the load-bearing constants hand-typed
 * into the diagrams still match their REAL sources, so the diagrams cannot
 * silently drift from the code.
 *
 * Rules that keep this trustworthy:
 *   1. Read the actual source file (YAML / Python / JSON), not a summary.
 *   2. FAIL LOUD if a source constant can't be located — a check that passes
 *      because it couldn't look is worse than no check.
 *
 * build.mjs auto-runs this because the file is named exactly verify-facts.mjs.
 *
 *   node scripts/diagrams/verify-facts.mjs          # standalone (exit 1 on mismatch)
 *   import { verifyFacts } from "./verify-facts.mjs" # returns string[] of problems
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const REPO = join(here, "..", "..");

/** All searchable text for one diagram: node titles + sub-lines + the human-facing meta. */
function diagramText(it) {
  const nodes = Object.values(it.spec.nodes).flatMap((n) => [n.title, ...(n.sub || []), n.chip?.text].filter(Boolean));
  const meta = [it.meta.heading, it.meta.blurb, it.meta.footnote, it.meta.extraHtml,
                ...(it.meta.legend || []).map((l) => l.label),
                ...(it.meta.edgeLegend || []).map((l) => l.label)].filter(Boolean);
  return [...nodes, ...meta].join("  ");
}

function read(relPath) {
  try { return { txt: readFileSync(join(REPO, relPath), "utf8") }; }
  catch (e) { return { err: `could not read ${relPath} (${e.message})` }; }
}

/** Match a regex in a source file; return the first capture or an {err}. */
function srcMatch(relPath, re, label) {
  const r = read(relPath);
  if (r.err) return { err: r.err };
  const m = r.txt.match(re);
  return m ? { value: m[1] } : { err: `${label} not found in ${relPath} (source moved or renamed?)` };
}

export function verifyFacts(items) {
  const problems = [];
  let hay = items.map(diagramText).join("  ");
  try {
    hay += "  " + readFileSync(join(REPO, "docs/architecture/index.html"), "utf8");
  } catch (e) {
    // Only the expected "not built yet" case is silent. A permission error,
    // a bad path, or anything else that isn't "the file doesn't exist" means
    // this verifier can't see what it's supposed to check — fail loud.
    if (e.code !== "ENOENT") throw e;
  }
  const has = (s) => hay.includes(s);

  // (1) LLM models — from src/unified_pipeline/config/llm_config.yaml.
  //     Default model (full id) and the human-friendly "Sonnet 4.6" we display.
  const def = srcMatch("src/unified_pipeline/config/llm_config.yaml",
    /default:[\s\S]*?model:\s*([\w.\-:]+)/, "default model");
  if (def.err) problems.push(`model: ${def.err}`);
  else if (!def.value.includes("claude-sonnet-4-6"))
    problems.push(`model: default is "${def.value}", diagrams say Sonnet 4.6`);
  if (!has("Sonnet 4.6")) problems.push(`model: "Sonnet 4.6" not shown in any diagram`);

  // Stage 3b override → Haiku 4.5.
  const s3b = srcMatch("src/unified_pipeline/config/llm_config.yaml",
    /stage_3b:\s*\{model:\s*([\w.\-:]+)\}/, "stage_3b model");
  if (s3b.err) problems.push(`model: ${s3b.err}`);
  else if (!s3b.value.includes("claude-haiku-4-5"))
    problems.push(`model: stage_3b is "${s3b.value}", diagrams say Haiku 4.5`);
  if (!has("Haiku 4.5")) problems.push(`model: "Haiku 4.5" not shown in any diagram`);

  // (2) Per-pod concurrency cap default — pipeline/concurrency.py.
  const cap = srcMatch("web_interface/backend/app/pipeline/concurrency.py",
    /DEFAULT_MAX_CONCURRENT_RUNS\s*=\s*(\d+)/, "DEFAULT_MAX_CONCURRENT_RUNS");
  if (cap.err) problems.push(`concurrency: ${cap.err}`);
  if (!has("CVICHE_MAX_CONCURRENT_RUNS")) problems.push(`concurrency: env var not shown in any diagram`);

  // (3) Backend port 8000 — main.py uvicorn.run.
  const port = srcMatch("web_interface/backend/app/main.py",
    /port=(\d+)/, "uvicorn port");
  if (port.err) problems.push(`port: ${port.err}`);
  else if (port.value !== "8000") problems.push(`port: backend runs on ${port.value}, diagrams say 8000`);
  if (!has("8000")) problems.push(`port: "8000" not shown in any diagram`);

  // (4) Prod backend replicas + HPA bounds — prod overlay.
  const reps = srcMatch("k8s/overlays/prod/backend-patch.yaml", /replicas:\s*(\d+)/, "prod replicas");
  if (reps.err) problems.push(`replicas: ${reps.err}`);
  else if (reps.value !== "2") problems.push(`replicas: prod backend is ${reps.value}, diagrams say 2`);

  const hmin = srcMatch("k8s/overlays/prod/backend-hpa.yaml", /minReplicas:\s*(\d+)/, "HPA minReplicas");
  const hmax = srcMatch("k8s/overlays/prod/backend-hpa.yaml", /maxReplicas:\s*(\d+)/, "HPA maxReplicas");
  if (hmin.err) problems.push(`hpa: ${hmin.err}`);
  else if (hmin.value !== "2") problems.push(`hpa: minReplicas is ${hmin.value}, diagrams say 2`);
  if (hmax.err) problems.push(`hpa: ${hmax.err}`);
  else if (hmax.value !== "4") problems.push(`hpa: maxReplicas is ${hmax.value}, diagrams say 4`);

  const cpu = srcMatch("k8s/overlays/prod/backend-hpa.yaml", /averageUtilization:\s*(\d+)/, "HPA CPU target");
  if (cpu.err) problems.push(`hpa: ${cpu.err}`);
  else if (cpu.value !== "70") problems.push(`hpa: CPU target is ${cpu.value}, diagrams say 70`);

  // (5) Prod hostname — ingress JSON6902 patch in the prod kustomization.
  const host = srcMatch("k8s/overlays/prod/kustomization.yaml", /value:\s*(cviche\.weill\.cornell\.edu)/, "prod host");
  if (host.err) problems.push(`host: ${host.err}`);
  else if (!has(host.value)) problems.push(`host: "${host.value}" not shown in any diagram`);

  // (6) IRSA Bedrock role — service-account annotation (lives in the prod overlay patch).
  const role = srcMatch("k8s/overlays/prod/sa-patch.yaml", /role\/(cviche-bedrock-role)/, "bedrock role");
  if (role.err) problems.push(`irsa: ${role.err}`);
  else if (!has(role.value)) problems.push(`irsa: "${role.value}" not shown in any diagram`);

  // (7) Step registry really has 12 stages — count StepDefinition entries.
  const reg = read("web_interface/backend/app/pipeline/step_registry.py");
  if (reg.err) problems.push(`stages: ${reg.err}`);
  else {
    const n = (reg.txt.match(/StepDefinition\(/g) || []).length;
    if (n !== 12) problems.push(`stages: step_registry.py defines ${n} StepDefinition(s), diagrams say 12`);
  }
  if (!has("12-stage") && !has("12 stages")) problems.push(`stages: "12-stage" not shown in any diagram`);

  // (8) LLM-stage taxonomy. The step_registry uses_llm flags are STALE for
  // stage 2 (still marked non-LLM; 5b's flag was fixed by #523), so the
  // truth is taken from the stage modules themselves: a module is an LLM
  // stage iff it calls call_llm(). This enumerates ALL 12 canonical stages
  // (not just a hand-picked subset) so a stage silently gaining or losing
  // call_llm() is always caught, not just the ones someone remembered to list.
  const ALL_STAGES = [
    ["1a", "src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py"],
    ["1b", "src/unified_pipeline/stage_1b_hierarchy_mapper.py"],
    ["2", "src/unified_pipeline/stage_2_entry_extraction.py"],
    ["3a", "src/unified_pipeline/stage_3a_header_taxonomy_mapper.py"],
    ["3b", "src/unified_pipeline/stage_3b_entry_classifier.py"],
    ["4", "src/unified_pipeline/stage_4_field_extractor.py"],
    ["4.5", "src/unified_pipeline/stage_4_5_research_summary.py"],
    ["5", "src/unified_pipeline/stage_5_pubmed_enrichment.py"],
    // 5b's call_llm site moved to stage5b/lookup.py in the #523 split; the
    // old stage_5b_institution_enrichment.py is now a facade that re-exports it.
    ["5b", "src/unified_pipeline/stage5b/lookup.py"],
    ["5c", "src/unified_pipeline/stage_5c_teaching_formatter.py"],
    ["5d", "src/unified_pipeline/stage_5d_citation_formatter.py"],
    ["6", "src/unified_pipeline/stage_6_word_template.py"],
  ];
  // The two genuine non-LLM stages the "ten of twelve" claim depends on.
  const EXPECTED_NON_LLM = new Set(["1b", "5"]);

  if (ALL_STAGES.length !== 12)
    problems.push(`stages: ALL_STAGES in verify-facts.mjs lists ${ALL_STAGES.length}, not the canonical 12 — fix the verifier itself`);

  let llmCount = 0;
  for (const [id, rel] of ALL_STAGES) {
    const r = read(rel);
    if (r.err) { problems.push(`stage${id}: ${r.err}`); continue; }
    const callsLlm = /call_llm\(/.test(r.txt);
    if (callsLlm) llmCount++;
    const shouldBeLlm = !EXPECTED_NON_LLM.has(id);
    if (callsLlm !== shouldBeLlm) {
      problems.push(shouldBeLlm
        ? `stage${id}: ${rel} no longer calls call_llm — recount the LLM stages`
        : `stage${id}: ${rel} now calls call_llm — it was a non-LLM stage; recount`);
    }
  }
  if (llmCount !== 10)
    problems.push(`stages: ${llmCount} of ${ALL_STAGES.length} stage modules call call_llm(), diagrams say ten`);
  if (!(has("Ten of the") || has("ten of its twelve") || has("LLM (10 stages)")))
    problems.push(`stages: diagrams should state that ten of the twelve stages call an LLM`);
  for (const stale of ["Seven stages", "Eight stages", "LLM (7 stages)", "LLM (8 stages)"])
    if (has(stale)) problems.push(`stages: a diagram still shows the stale count "${stale}" (should be ten)`);

  // (9) ROR is deprecated — institution enrichment (5b) now uses an LLM, not the ROR
  // API (there is no live ror.org call in src/). No diagram should reference it.
  for (const s of ["ROR API", "via ROR", "via the ROR"])
    if (has(s)) problems.push(`ror: a diagram still references "${s}" (stage 5b enriches via LLM now)`);

  return problems;
}

// Standalone run: load the definitions, verify, report, set exit code.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const dir = join(here, "definitions");
  const items = [];
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".mjs")).sort()) {
    const mod = await import(join(dir, f));
    items.push({ spec: mod.spec, meta: mod.meta });
  }
  const problems = verifyFacts(items);
  if (problems.length) {
    console.error(`FAIL facts: ${problems.length} mismatch(es)`);
    problems.forEach((p) => console.error("    " + p));
    process.exitCode = 1;
  } else {
    console.log("PASS facts: every probed constant matches its source");
  }
}
