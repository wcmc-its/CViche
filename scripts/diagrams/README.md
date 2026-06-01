# Architecture diagrams

Polished, **version-controlled** architecture diagrams for CViche, rendered from
plain-data spec files by a small dependency-free Node toolkit into self-contained
SVGs + PNGs + one `index.html` gallery. No Mermaid, Graphviz, draw.io, layout
engine, or `npm install`.

## Build

```bash
node scripts/diagrams/build.mjs
```

Writes to `docs/architecture/`:

- `<id>.svg` — the committed vector (renders on GitHub, crisp at any zoom)
- `<id>.png` — raster for Slack/Teams/slides (gitignored; needs `rsvg-convert` or `sharp`)
- `index.html` — a branded gallery (⌘/Ctrl+P → Save as PDF for decks)

The build validates geometry, renders, rasterizes, assembles the gallery, then runs
the fact check (below). It exits non-zero on any geometry or fact problem, so it can
gate CI.

## The four views

| File | View |
|------|------|
| `definitions/01-context.mjs` | System context — sources, the two entry points, external services |
| `definitions/02-pipeline.mjs` | The 12-stage CV pipeline — model/API per stage, data flow |
| `definitions/03-runtime.mjs` | Web app & run lifecycle — upload → admission gate → background run → live progress |
| `definitions/04-deployment.mjs` | Deployment topology — EKS, ALB ingress, HPA, IRSA → Bedrock |

## Files

| File | Role | Project-specific? |
|------|------|-------------------|
| `lib.mjs` | renderer: palette, `A()` anchors, `renderSVG()`, `validate()` | no — copied verbatim from the skill |
| `build.mjs` | validate → SVG → PNG → gallery; the `buildHtml()` REBRAND block is CViche-specific | mostly no |
| `check-crossings.mjs` | dev linter for edges that tunnel through a foreign node box | no |
| `verify-facts.mjs` | asserts diagram constants still match the real sources (tier 3) | **yes** |
| `definitions/*.mjs` | the four views, as plain data | yes |

## Verify (four tiers)

1. `node scripts/diagrams/build.mjs` — geometry (`validate()`) + facts, automatic.
2. `node scripts/diagrams/check-crossings.mjs` — edges tunnelling through a foreign node box.
3. `node scripts/diagrams/verify-facts.mjs` — diagram constants vs real source. It probes:
   the LLM models (`src/unified_pipeline/config/llm_config.yaml`), the per-pod run cap
   (`web_interface/backend/app/pipeline/concurrency.py`), the backend port (`main.py`),
   the prod replicas + HPA bounds + host + IRSA role (`k8s/overlays/prod/*`), and the
   12-stage count (`web_interface/backend/app/pipeline/step_registry.py`). It **fails loud**
   if a source constant can't be found, so a refactor that moves it trips the check instead
   of silently passing.
4. **Look at the rendered PNG / `index.html`.** No automated check catches a label sitting on a line.

## Editing

Each view is pure data: edit coordinates/labels in `definitions/NN-*.mjs` and rebuild.
When the system changes, update the relevant `meta.source` line and the matching probe in
`verify-facts.mjs`, then rebuild. Keep `lib.mjs` in sync with the upstream skill rather than
diverging it per diagram.

Method and rationale: `~/.claude/skills/architecture-diagrams/references/PLAYBOOK.md`.
