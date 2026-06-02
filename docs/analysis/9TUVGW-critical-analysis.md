# Critical Analysis — Run 9TUVGW (and systemic findings)

**Date:** 2026-06-02
**Inputs:** real S3 artifacts `s3://wcm-cviche-storage/cviche/runs/9TUVGW/` (12 stage outputs + `9TUVGW_wcm.docx` + ~1000 prompt logs), live `cviche-dev` cluster, and the source tree at `origin/dev`.
**Method:** multi-agent investigation with an adversarial verification pass per finding; the SAML and migration findings were additionally confirmed against live pod logs. Each item is tagged **[VERIFIED]** (live log / artifact evidence) or **[INFERRED]** (code trace).

> Note on the bucket: run outputs **are** durably archived to S3 (`cviche/runs/{id}/…`). The web run-viewer's 400s are a URL-construction bug, **not** data loss.

---

## Summary & status

| # | Issue | Severity | This PR |
|---|-------|----------|---------|
| E | `name 'response' is not defined` corrupts stage_3b cost/stats on ~100% of runs | High | **Fixed** |
| A | Run-viewer spams `[SECURITY]` 400s every ~2s; CV-insights card never loads | Medium | **Fixed** |
| D | Generated `.docx`: raw `\t` tabs, prompt-echo bullets, mis-routed content | High | **Partly fixed** (see Accuracy fixes) |
| Acc | Misclassification routing + template-scaffold leakage (root of D, T-bucket, sparse fields) | High | **Fixed (deterministic, validated)** |
| C | New image CrashLoops; migration/scale architecture incoherent with SQLite | Critical (dev rollout blocker) | **Diagnosed only** — active parallel work |
| B | SAML assertion accepted **despite** failing signature verification | Medium (security) | **Diagnosed only** — auth, needs care |

Plus: a per-run **LLM cost** breakdown, and a **quality scorer** (`src/unified_pipeline/quality_score.py`) shipped advisory-only.

**Why C and B are diagnosis-only:** both sit inside active, sensitive work (Mahender has 5 migration commits today; B is auth where a wrong "fix" causes an outage). Precise root-cause is more useful than a competing commit. Details below.

---

## Accuracy fixes (implemented + validated deterministically against 8 real S3 runs, no LLM re-run)

The user's directive was to improve **accuracy**, not suppress errors. Three converging root causes were found; the deterministic, validatable ones are fixed here. The deepest one (over-segmentation) changes LLM inputs and is flagged eval-needed.

**1. WCM structured-table correctors** (`core/validators/wcm_table_corrector.py`, wired into stage_3b step 10b). Content-keyed, deterministic post-classification corrections, only overriding a small allow-list of known-wrong source codes:
- Mentee table (`Mentoring Period` + `Type of Supervision`/`Site/position`) → **N3A/N3B** (was K2).
- Board certification (`American Board of …` / board-table labels **with** a cert number/date) → **F2** (was I).
- Licensure (`DEA/NPI/License number`) → **F1**.
- *Validated on 9TUVGW:* 7 corrections, **0 false positives** — the 2 real board-cert blocks (I→F2) and all 4 mentee tables (K2→N3A). This is the root fix for the "tab in Section I / mentees as bullets" symptom: mentees now route to the mentee **table** (proper columns) instead of tabbed bullets.

**2. Template-scaffold corrector** (`core/validators/template_scaffold.py` + `wcm_template_scaffold_strings.json`, stage_3b step 10c). The source CVs are filled WCM templates; leftover instruction text leaks in as content. Entries whose normalized text matches the **blank** template (exact, or ≥0.95 ratio for long sentences) are recoded to **T** so downstream stages drop them.
- *Validated on 9TUVGW:* 30 recodes — the prompt-echo bullets ("Clinical teaching (bedside…)" K2→T, "Administrative teaching…" K3→T), hospital-affiliation labels, visa questions. Non-WCM-template CVs got 0–2, so it does not over-fire. Net code distribution: **T +30, K2 −7, K3 −3, F2 +3, N3A +4**.

**3. Tab rendering fallback** (`stage_6_word_template._clean_inline_tabs`, applied at the 3 bullet emit sites). Residual `Label\tValue` bullets render as "Label: Value" instead of a ragged naked tab. With fix #1 the main offenders no longer reach this path; this is the backstop.

### Accuracy fixes deferred — need a gold/eval run (documented, NOT committed, to honor "don't claim accuracy without eval")
- **Over-segmentation / hierarchy fan-out (the disease behind 58% duplicates + misclassification).** Stage 1b emits overlapping section ranges (`stage_1b_hierarchy_mapper.py:446` repair only handles `end < start`); Stage 2 re-extracts the same indices once per overlap (`stage_2_entry_extraction.py:1052`, no cross-section guard). Fix: clamp overlapping ranges in 1b / skip already-assigned indices in 2. **Changes which section wins a contested index → changes downstream classification → must be eval'd that no real content is lost.** Highest-leverage next step.
- **Cross-code duplicate removal.** Stage 3b flags `is_duplicate` but keys on first-100-chars + same-code, missing ~19 cross-code copies that reach the docx; stage 6 dedups within-code only. Fix: key dedup on normalized full text across all codes, and drop (not just flag). Eval to confirm no false-merge.
- **Add F1/F2 to the classifier prompt** (currently absent — 0 occurrences), so the LLM has the option natively rather than relying on the corrector. Prompt change → eval.
- **Field-schema overflow + recovery gate** (`stage_4_field_extractor.py`): re-add `narrative`/`institution` overflow to `minimal` schemas; lower the `min_original_chars=200` recovery threshold. Eval.

---

## E — stage_3b `NameError` corrupts cost/stats  [VERIFIED] — fixed here

`validate_t_classifications` and `reconnect_fragments` read `response.usage.prompt_tokens` (stage_3b_entry_classifier.py:1420, 1602), but the LLM result is stored in `llm_result`; `response` is never bound → `NameError`. It throws **after** the reclassification loop already mutated `entries`, so data is fine but the `except` returns `cost:0.0, reclassified:0, error:"name 'response' is not defined"`. 9TUVGW's `classified.json` shows 56 reclassifications actually applied while stats reported 0.

**Fix (this PR):** use `llm_result["prompt_tokens"|"completion_tokens"|"cost"]` (also drops a dead "gpt-5.1" cost formula in favour of the client's real priced cost); hardened both `except` blocks to report counts already applied. This also removes the error string that hard-caps the quality score on every run.

## A — run-viewer 400 spam  [VERIFIED] — fixed here

`PipelineViewer.tsx` `loadCvInsights` passed the **absolute** path from `Step.output_files` straight into `getRunDataJson` → URL `…/data//app/.../9TUVGW_fields.json/json`; the backend path-guard (`steps.py:97`) correctly rejects absolute paths with 400. The 2s status poll produces a fresh `steps` array each tick, re-firing the effect forever (the prior basename fix only patched `OutputFiles.tsx`).

**Fix (this PR):** centralize basenaming inside `getRunDataJson` (`runs.ts`) so no caller can leak a path, plus a run-once `useRef` guard (reset per `runId`) so a failure can't re-spam every poll.

> Out of scope: the `/json` viewer route has no S3 fallback, so after a pod recycle the insights card 404s even with this fix. Pre-existing; tracked separately.

## D — broken WCM `.docx`  [VERIFIED] — diagnosed

Three mechanisms in the real `9TUVGW_wcm.docx`:
1. **Raw `\t`** carried verbatim into single bullet paragraphs (e.g. `• Mentoring Period\t08/2022 - Present`) with no tab stops — emitted at 3 sites in `stage_6_word_template.py` (~:1446, :2780, :7078).
2. **Prompt-echo**: template instruction text emitted as bullet content (`_is_structural_label` misses mixed-case sublabels, ~:1697-1723).
3. **Mis-routed content**: board/award blocks dumped into the memberships table (`_parse_multi_membership_entry` splits on `\n`/`|` not `\t`, exit ~:4530-4532).

Stage-6 guards (strip/convert `\t` + tab stops; sublabel allow-list; reject `\t`-laden board/award blocks) hide the corruption but **the root is upstream misclassification** (board/award→`I`, mentoring→`K2` in stage 3/3b). Fix both. Not committed here (touches the classifier hot path; size/risk warrants its own change + eval).

## C — CrashLoop + scale architecture  [VERIFIED] — diagnosed only

The new image (`cce8d65`, = `origin/dev` HEAD) CrashLoops the `db-migration` init container:
```
==> Executing migrations with identity context:        # DB_USER empty
sqlite3.OperationalError: unable to open database file
```
Chain of causes, all confirmed live:
1. `cviche-secrets` provides **no** DB connection vars — the running pod has `DB_HOST/DB_PORT/DB_NAME/DB_USER` **all empty**. So the app (and migrations) use the **SQLite fallback** (`database.py:62`, `alembic/env.py:64` → `./cviche_dev.db`).
2. The init container has **no `env`/`envFrom`** at all (base `deployments.yaml:19-24`); the secret is patched only onto the `backend` container — so even intended MySQL config would never reach migrations.
3. The Dockerfile chowns only the output/upload dirs, **not** `/app/web_interface/backend` (confirmed `drwxr-xr-x root root`). The non-root `cviche` user (USER cviche) cannot create `cviche_dev.db` there → "unable to open database file".
4. **Architectural:** the init-container migration pattern assumes a *shared* DB. With SQLite-on-ephemeral-local-fs it is incoherent — the init container and main container don't share a writable layer, and `replicas>1` (prod overlay, not on dev) would split-brain across per-pod SQLite files + in-process event broker. The prod `backend-patch.yaml` comment already flags the Redis requirement.

This is the target of the 5 consecutive "added different user for the migrations" commits. **Recommended direction (coordinate with Mahender):**
- Decide the DB story explicitly: either provision a real shared DB (RDS/MariaDB) and put `DB_HOST/PORT/NAME/USER` in `cviche-secrets` (then the init-container migration pattern is correct), **or** if SQLite-per-pod is intentional for dev, drop the init container and chown the SQLite dir + keep `replicas:1`.
- For scale-out: shared DB **+** `CVICHE_STORAGE_BACKEND=s3` (bucket exists) **+** `CVICHE_REDIS_URL` are all prerequisites before `replicas>1`.
- The `db-migration` init container is injected/templated by the external CD layer too — repo-only changes won't fully fix it. [INFERRED]

## B — SAML signature not enforced  [VERIFIED] — diagnosed only

Live ACS sequence (2026-06-02 12:00) for user `paa2013@med.cornell.edu`:
```
ERROR saml2.sigver ... EVP_VerifyFinal: signature verification failed   (Response)
ERROR saml2.sigver ... EVP_VerifyFinal: signature verification failed   (Assertion)
INFO  saml2.response Subject NameID: paa2013@med.cornell.edu
POST /api/saml/acs 302        # success
GET  /api/auth/me   200       # authenticated
```
Both signatures fail (one cert attempt each — not multi-cert rollover), yet the login **succeeds**. With `want_assertions_signed: True` configured (`saml_client.py:144`, "SEC-02: reject unsigned assertions"), an assertion whose signature does not verify is being **accepted** — an auth-integrity gap, not the login *outage* originally hypothesized.

Two things to fix, **in this order** (reversing them causes an outage):
1. Root-cause the signing-key/metadata mismatch so signatures actually verify — diff the live assertion's `ds:X509Certificate` fingerprint against the certs served by the configured `saml_idp_metadata_url`.
2. **Then** make verification fatal (fail fast) so a non-verifying assertion is rejected.
Also: the prod SP cert/key + `auth_config.yaml` (`k8s/overlays/prod/`) are 0-byte placeholders — provision before any prod SAML, and apply auth changes to the live ConfigMap, not the repo placeholders.

---

## Per-run LLM cost

**≈ $2.71/run** (pipeline reports $2.77 due to a stage_3b pricing-key bug — `_normalize_model_id` strips `us.` but not the `-20251001-v1:0` suffix → PRICING miss). 334 calls; tokens from real `usage` fields.

| Stage | $/run | % |
|-------|------|---|
| stage_2 entry parser (Sonnet, 86 calls) | $1.11 | 41% |
| stage_4 field extractor (Sonnet, 130 calls) | $0.92 | 34% |
| stage_5d citations | $0.25 | 9% |
| stage_3b (Haiku, 47% cache hit) | $0.08 | 3% |

**Levers (ranked):** (1) stage_2→Haiku ~40% — gate on eval, errors cascade; (2) stage_4→Haiku ~33% — eval across field types; (3) terminal 5b/5c/5d/6→Haiku ~13%, low risk; (4) prompt-cache stage_2/stage_4 schema; (5) fix the PRICING key (reporting only). The stage_3b precedent (Haiku beat Sonnet on accuracy) suggests 1–2 may eval favorably.

---

## Quality scorer

`src/unified_pipeline/quality_score.py` — deterministic 0–100 from artifacts only (no LLM calls). 7 weighted dimensions, two hard-fail caps (pipeline error → 40; missing CV-owner name → 25). CLI: `python3 quality_score.py <outputs_dir> [run_id] [--gate]`; importable `score_run()` / `quality_gate(mode="off|advisory|block")`.

**Calibration (8 S3 runs):** all scored **25–40 (RED)**. Dominated by (1) the cluster-E error string (hard-fail cap 40 on ~100% of runs — removed by the E fix in this PR) and (2) systemic docx/duplicate penalties. The score discriminates within range, but **GREEN is not yet meaningful**; ship **advisory-only**, re-baseline after E + D are fixed and a human-confirmed clean run exists.

| Run | Score | | Run | Score |
|-----|------|--|-----|------|
| 9TUVGW | 27 | | EVZ1YW | 40 |
| 0GX6RA | 34 | | P2ZP1A | 25 |
| B7TFKA | 33 | | 7RHKJQ | 33 |
| M2D90G | 33 | | VFFDCA | 40 |

**Integration:** call `quality_gate()` after stage 6, write `{run_id}_quality_score.json` as an artifact, surface the breakdown in the run-viewer. Keep `mode="advisory"` until the re-baseline; flip to `block` (score < 60) only afterward.

---

## Remediation order

1. **C** — decide DB story + unbreak the migration init container (with Mahender). Dev rollout blocker.
2. **E** — *this PR.*
3. **A** — *this PR.*
4. **B** — fix cert/metadata mismatch, *then* enforce signatures.
5. **D** — stage-6 guards **+** upstream classification fix.
6. Scale-out prerequisites (shared DB + S3 + Redis) before `replicas>1`.
7. Cost levers + PRICING-key fix + flip the quality gate to blocking.

## Open items not yet root-caused
- **Duplicate-entry ratio ~58%** on 9TUVGW (entries.json over-counts ~156%) — points at segmenter/classifier fragmentation; warrants its own investigation.
- Why the 19h `cviche-dev` pod serves on SQLite with no visible `.db` (likely an older root-running image) — confirms the non-root image is what newly breaks SQLite write.
