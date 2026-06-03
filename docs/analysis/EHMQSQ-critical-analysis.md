# Critical Analysis — Run EHMQSQ

**Date:** 2026-06-03
**Inputs:** real S3 artifacts `s3://wcm-cviche-storage/cviche/runs/EHMQSQ/` (12 stage outputs + `EHMQSQ_wcm.docx` + 423 prompt-log files = 141 request/RESPONSE/READABLE triplets), the live `cviche-dev` cluster (context `arn:aws:eks:us-east-1:665083158573:cluster/reciter`), and the source tree at `origin/dev` plus the producing source commit `fef247e` (= image `dev-123`).
**Producing image:** the serving backend pod `cviche-backend-68f57cc957-4mpr2` (image `backend:dev-123.2026-06-01.16.16.42.fef247e5`, started 2026-06-01T16:18:20Z, 0 restarts, 1/1) has run continuously ~44.7h; EHMQSQ outputs were written 2026-06-02 16:06–16:17 (20:09 UTC `meta.generated_at`), inside that window. **EHMQSQ was therefore produced by pre-#85 code.** The newer `dev-136` backend (= `origin/dev` HEAD `e779b899`, carrying the #85/#92 fixes) is `Init:CrashLoopBackOff` and never served any run.
**Method:** multi-stream investigation with an adversarial verification pass per finding; every load-bearing number was re-derived from quoted command/artifact output. Items are tagged **[VERIFIED]** (live log / artifact / command output) or **[INFERRED]** (code trace).

> Note on segmentation: unlike 9TUVGW, EHMQSQ has **zero** duplicate content entries and **100.0%** clean coverage. It is a clean-CV counter-example to the over-segmentation cluster.

---

## Summary & status

| # | Issue | Severity | Status on EHMQSQ |
|---|-------|----------|------------------|
| **Deploy** | Backend rollout frozen at `dev-123`; `db-migration` init container CrashLoops on empty DB config; FE skewed to `dev-136` | **Critical** | **Live blocker** — none of the 06-02 backend fixes serve the pilot |
| E | stage_3b `name 'response' is not defined` zeroes cost/stats and hard-caps quality at 40 | High | **Present** — fix merged on dev, NOT deployed |
| D (scaffold) | Generated `.docx` ships all 11 WCM instruction-box sentences + employment-status menu as content | High | **Present (severe)** — #85 `template_scaffold` fix merged, NOT deployed |
| D (tabs/routing) | Raw `\t` in bullets; mis-routed board/award/mentee content | High | **Mostly NOT reproduced** on this CV (residual tabs in 3 body paras + 2 table cells only) |
| Acc | Misclassification routing + template-scaffold leakage | High | **Partial** — 5 prose K2 mentoring roles uncorrectable by #85; tables routed correctly |
| Seg | Over-segmentation / duplicate fan-out | Medium | **NOT reproduced** — 0 duplicates, 100.0% coverage |
| Cost | Per-run LLM cost | Info | **$1.21/run**, 141 calls |

Plus: an advisory **quality scorer** result of **40/100 (RED)**.

**Why nothing is "fixed here":** every relevant fix (#85 correctors, the E NameError fix) is already merged to `origin/dev` but is in the `dev-136` image that cannot roll out. EHMQSQ is a pre-#85 artifact. The single corrective action is to unblock the rollout, then re-run.

---

## Deploy — backend rollout frozen at dev-123  [VERIFIED]

The serving pod is `dev-123` (built 2026-06-01 16:16), 0 restarts, ~44.7h continuous — predating every 06-02 PR (#85 merged ~15:21 UTC on 06-02, ~23h later). Every newer backend ReplicaSet (`dev-128`…`dev-136`) shows `readyReplicas=0`; the deploy condition is `Progressing=False :: ReplicaSet "cviche-backend-6758b9f7d5" has timed out progressing` while `Available=True` via the old RS. The frontend rolled forward to `dev-136` successfully (`Progressing=True`), producing a **13-build version skew** (FE `dev-136` vs BE `dev-123`).

**Root cause:** the `db-migration` init container resolves `DB_HOST/PORT/NAME/USER` to `''`. Init log (pod `cviche-backend-6758b9f7d5-qhq7l`): `==> DB Factory: Compiling Engine -> HOST: '', PORT: '', USER: ''` then `RuntimeError: Database factory received incomplete configurations! Given: HOST='', PORT='', USER='', NAME=''` at `app/database_factory.py:16` via `alembic/env.py:72`. The init container has only `MIGRATION_USER` (configMapKeyRef `MIGRATE_USER`), no `envFrom`, no `auth_config.yaml` mount. DB params live only inside `auth_config.yaml` (cm `cviche-auth-config-m75bcch996`: `DB_HOST: cviche.cetg9yc1lyuf.us-east-1.rds.amazonaws.com`, `DB_PORT: 3306`, `DB_USER: cviche_app_user`, `DB_NAME: cviche`), mounted as a file on the **main** container only.

This is a **repo-manifest gap** (`k8s/base/backend/deployments.yaml:19-30` + `k8s/overlays/dev/backend-patch.yaml`, which mounts the volume only under `containers: name: backend`), fixable in-repo — not an external CD layer. The factory previously fell back to SQLite; Mahender's refactor (`b2612bf`, "refactored the create_cviche_engine to access all configuration params") correctly hard-raises, which surfaced the long-standing wiring gap as a crash. *Correction vs an earlier note:* the namespace has 13 configmaps (not 12); `cviche-secrets-676fdf9b94` (the referenced secret) has no DB key, though some unreferenced secrets contain `CVICHE_DATABASE_URL` — the init container consumes none of them.

## E — stage_3b NameError corrupts cost/stats  [VERIFIED]

`meta.stats.t_validation = {t_entries_reviewed:3, t_entries_reclassified:0, cost:0.0, error:"name 'response' is not defined"}` and `fragment_reconnection = {fragments_reviewed:1, fragments_reconnected:0, cost:0.0, error:"name 'response' is not defined"}`. Yet the data **was** mutated: exactly one entry — **array idx 90** (`taxonomy_code=R`, conf 0.75, "Communication & Communication Reboot Workshops, WCM") — carries the prefix `[T-validation reclassified from T]`. The prompt log `prompt_logs/2026-06-02_20-09-48_stage_3b_..._RESPONSE.json` proves the call ran (system prompt: "CRITICAL REVIEW of entries that were initially classified as 'T'"), `elapsed 4.871s`, model `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `usage={prompt_tokens:2689, completion_tokens:409, total_tokens:3098}`, reclassifying entry_index 90 → R (idx 69/142 confirmed-as-T, kept). The NameError throws *after* the mutation, so data survives but the `except` zeroes cost/reclassified and writes the error string.

On the frozen `fef247e` source, stage_3b reads `response.usage.prompt_tokens` where `response` is never bound → NameError. On `origin/dev` (`e779b899`) the same sites read `llm_result["prompt_tokens"|"completion_tokens"|"cost"]` (3 fixed sites, 0 `response.usage`). This is the identical 9TUVGW cluster-E signature. The error string is what **hard-caps the quality score at 40**.

*Correction vs an earlier draft:* the reclassified entry is at **array idx 90**, not idx 233 (out of range; only 159 entries).

## D — generated `.docx` quality  [VERIFIED]

Reproduces 9TUVGW cluster-D **partially**. The dominant defect is **template-scaffold leakage** (severe); the raw-tab-in-bullet and mis-routing subclusters largely did **not** reproduce.

**Scaffold leakage (severe):** all **11/11** WCM instruction-box sentences appear in the final docx as numbered bullets, e.g. `[0]` "Retain the format of the CV template throughout…", `[9]` "You may delete all instructional information…", `[10]` "Upon completion, please delete this instruction box…". The full **employment-status menu** also leaks: "Current Employment Status (Please choose one, list here, delete the others):" followed verbatim by all 8 mutually-exclusive options (Full-time salaried by Weill Cornell … Sessional). `classified.json` has exactly **1** entry coded T, so the blank-template scaffold was never recoded/dropped — this is exactly what #85's `template_scaffold.py` corrector (recode-to-T) eliminates, and it is absent from the producing image.

**Residual tabs (low):** 0 tab-in-bullet defects (mentoring/teaching bullets render clean inline " - " even though 5 of the 7 K2 source entries contain literal `\t`). Residual raw tabs survive only in **3 body-level paragraphs** (Signature line; "Name of Current Employer(s):\t"; "Current Employment Status (…):\t") **and 2 TABLE 7 cells** (`\tInstitution, city and state`; `\t`) — 5 distinct tab-artifact paragraphs total. *Correction:* body-level tab paragraphs = 3 (not 4); the "Institution, city and state" leading-tab item is one of the 2 table cells, not a separate body paragraph.

**Empty tables:** **12 of 34** tables are header-only (T0 instruction-box, T6, T7, T23–T30, T33) — 11 if T0 is excluded as the instruction box. T23/T24/T25 are three identical blank "Name of Committee | Role | Organization | Dates" tables stacked back-to-back. *Correction:* 12 (or 11), not the earlier 10.

**Mis-routing NOT reproduced:** "American Board of Internal Medicine" → F2 (not mis-routed to I); memberships (I=2) genuine societies; mentee blocks → N3A/N3B in proper tables; CV-owner name correct ("Name: Alice J. Tang, MD, MHPE"). Unlike 9TUVGW (7 table-corrector fixes), this CV's structured tables routed correctly.

## Acc — residual misclassification beyond #85's reach  [VERIFIED]

#85's deterministic correctors made **0 corrections / 0 false positives** on EHMQSQ — a positive conservative-gating validation (this clean CV has no WCM mentee-table signatures and no echoed scaffold text). But **5 real residual misclassifications** exist that #85 cannot reach: prose mentoring/coaching roles under ADVISING & MENTORING coded **K2** that should be **N3A/N3B**, at **array idx 135–140** (Annual Faculty Reviewer; TEACH Program/Mentor; Faculty Remediation Coach; Master Coach; Peer Mentoring Program). They are prose, not the WCM mentee TABLE, so the corrector's `_MENTEE_PERIOD` + `_MENTEE_SUPPORT` gate never both match. Separately, mentee posters in the "Presentations by Mentees:" block are inconsistently coded **S8 vs N4** (`meta.qa_flags.hierarchy_mismatches` = 36 flagged, flag-only, includes element_idx 375/377 as ADVISING & MENTORING assigned S8 expected [K2,N]). This is the deferred "classifier-prompt" work, not a #85 regression. *Correction:* the K2 roles are at array idx 135–140 (earlier-cited 342/344/347/350/355 mixed indexing systems); licensure entries are at array idx 8/9/10.

## Seg — over-segmentation NOT reproduced  [VERIFIED]

`meta`: `total_entries:159, duplicate_entries:0, unique_entries:159`. `entries.json` coverage: 299 = 159 content + 11 header + 129 break, `assigned_indices:397`, `unaccounted_indices:[]`, `coverage_percentage:100.0`. All 129 break entries are empty padding (the 299 headline is ~2x inflation by blank-line padding, not duplication). Index-level walk: 0 indices assigned to >1 entry. `hierarchy_mapped.json` boundaries are perfectly contiguous/non-overlapping (0-4, 5-17, …, 379-396) — so the documented stage_1b-overlap/stage_2-no-guard fan-out **never fired**. By contrast 9TUVGW had ~400 re-extracted indices (398–405 depending on string sub-row index handling, up to 3x) and 155.9% coverage. [INFERRED on the code mechanism: `stage_1b_hierarchy_mapper.py:446-470` only repairs `end < start`; the `stage_2_entry_extraction.py` loop subtracts no `all_assigned_indices` before extraction.]

---

## Per-run LLM cost

**$1.21/run** over 141 LLM calls (all 141 RESPONSE logs present, 0 zero-cost). Lower than 9TUVGW's $2.71 purely because EHMQSQ is a smaller CV; the stage shape is the same (top-3 = 77.6% of cost).

| Stage | calls | $/run | % | model |
|-------|------:|------:|--:|-------|
| stage_4 field extractor | 78 | $0.4698 | 38.8% | sonnet-4-6 |
| stage_3b classifier | 20 | $0.2527 | 20.8% | haiku-4-5 (71,836 cache_read tok) |
| stage_2 entry parser | 15 | $0.2177 | 18.0% | sonnet-4-6 |
| stage_5d citations | 2 | $0.0766 | 6.3% | sonnet-4-6 |
| stage_5c | 1 | $0.0671 | 5.5% | sonnet-4-6 |
| stage_3a | 1 | $0.0326 | 2.7% | sonnet-4-6 |
| segmentation_chunked_hierarchy | 1 | $0.0324 | 2.7% | sonnet-4-6 |
| stage_6 | 18 | $0.0193 | 1.6% | sonnet-4-6 |
| segmentation_signature | 3 | $0.0192 | 1.6% | sonnet-4-6 |
| stage_4_5 | 1 | $0.0134 | 1.1% | sonnet-4-6 |
| stage_5b | 1 | $0.0116 | 1.0% | sonnet-4-6 |
| **Total** | **141** | **$1.2124** | | |

**Cost-reporting bugs:** (1) `meta.stats.cost` for stage_3b = $0.2468696, under-counting the real $0.2527 by **$0.0058** because the NameError zeroes the t_validation + fragment_reconnection sub-step costs. (2) `meta.model = "gpt-5.1"` is a mislabel — the actual model is `us.anthropic.claude-haiku-4-5-20251001-v1:0`; the "gpt-5.1" string comes from a hardcoded `model: str = "gpt-5.1"` default recorded into `meta` while the runtime call routes to Haiku (not from a `_normalize_model_id` function, which does not exist in the tree). Per-call costs were correctly priced (`config.py:103` has the full-suffix PRICING key), so this is reporting-only. **Levers** (same as 9TUVGW): stage_4 (39%) and stage_2 (18%) are the prime Sonnet→Haiku migration candidates (gate on eval); prompt-cache the stage_2/stage_4 schema.

---

## Quality score

**40/100 — RED (re-run / do-not-deliver).** `quality_score.py` re-run on the S3 artifacts: `raw_score_before_caps=46.76`, `hard_fail_caps_applied=[40]`, `total_weight=95`.

| Dimension | Score / Max | Penalty | Detail |
|-----------|------------:|--------:|--------|
| Pipeline/API errors (HARD-FAIL) | 0 / 25 | 25.0 | fatal_pattern=YES; 2 non-null error fields = the cluster-E NameError → **caps total at 40** |
| CV owner name/contact (HARD-FAIL) | 15 / 15 | 0 | full_name="Alice J. Tang"; no owner cap |
| T-bucket share | 12 / 15 | 3.0 | T_count=1/159 (very low), +0.2 for t_validation_error present |
| Sparse tables in docx | 0 / 12 | 12.0 | 13/34 sparse, 79/392 empty cells |
| Broken format artifacts | 2.97 / 10 | 7.03 | raw_tab_paragraphs=3, echo_paragraphs=23 (scaffold leakage) |
| Field-extraction sparseness | 4.46 / 8 | 3.54 | 158 entries, 7 all-null, success 0.956 |
| Duplicate-entry ratio | 10 / 10 | 0 | dup_ratio=0.000, coverage 100.0% (much healthier than 9TUVGW) |

EHMQSQ (40) sits at the top of the observed 25–40 RED band. Even un-capped (46.76) it would be RED (<60) from the sparse-table + broken-format penalties. The cap is the cluster-E error string — which the merged-but-undeployed E fix removes.

---

## Remediation order

1. **Deploy (Mahender):** unblock the `dev-136` rollout — add the `auth-config-volume` mount (or `DB_HOST/DB_PORT/DB_NAME` env) to the `db-migration` init container in `k8s/base/backend/deployments.yaml` / `overlays/dev/backend-patch.yaml`. Keep the hard-raising factory; fix the wiring, don't revert. This single fix makes #85 (Acc + scaffold), the E NameError fix, the admin Score route, and all other 06-02 backend behavior live.
2. **Re-run EHMQSQ** on `dev-136` once it serves, and re-score — current output is do-not-deliver.
3. **Acc deferred (Paul):** extend the classifier prompt / a prose-mentee corrector signature (role label "Mentor"/"Coach" + named mentee + ADVISING & MENTORING → N3A/N3B), gated on eval — the 5 K2 roles #85 can't reach.
4. **Cost (Paul):** fix the `gpt-5.1` `meta.model` mislabel and stage_3b cost under-count (subsumed by the E fix); evaluate stage_4/stage_2 Sonnet→Haiku.
5. **Re-baseline the quality gate** to blocking only after a human-confirmed clean run exists post-rollout.