# Retry / Resume Durability — Architecture Plan

**Date:** 2026-06-09
**Trigger:** Run `B2RRRA` — original run hung, user retried the failed step (Stage 6),
got `Pipeline failed — No input available for Stage 6`.
**Goal:** Change the architecture so per-step retries reliably resume going forward,
surviving pod recycles.

---

## 1. Root cause (confirmed from code)

1. **Retry resumes, it does not recompute.** A per-step retry re-runs the failed step
   and everything after it, reusing earlier stages' outputs read straight off the pod's
   **local disk** (`orchestrator.py:386` `_prepare_resume`; skip loop `:430-432`).
2. **The retried step was Stage 6 (step 12, WCM Word template).** Stage 6's
   `ValueError("No input available for Stage 6")` (`orchestrator.py:1056`) can *only* fire
   when the resume starts at step 12 — any earlier start re-runs an intermediate stage,
   which always registers its output in `self.stage_outputs`. The retry button targets the
   first `status === 'error'` step (`PipelineViewer.tsx:205`).
3. **Upstream outputs (5d/5c/5b/5/4) were gone from local disk.** Otherwise
   `_prepare_resume` would have rehydrated stage 5d (`:399-401`) and Stage 6 would have
   found it.
4. **Why gone:** EKS writes pipeline outputs only to the pod's ephemeral filesystem
   (`src/unified_pipeline/outputs/`, `orchestrator.py:246`). The deployment never sets
   `CVICHE_STORAGE_BACKEND`, so it defaults to `"local"` — the S3 mirror
   (`_persist_outputs_to_storage`, `:370-371`) and the upload archive
   (`upload.py:222`) are both **no-ops**. A pod restart/redeploy between the original run
   and the retry wiped every intermediate output. (Recent merges #131/#132/#133 = a likely
   redeploy.)
5. **Cryptic message:** the recovery path that should say *"earlier results are no longer
   available — Restart with this file"* (`:488-494`) only triggers on `FileNotFoundError`
   / "no such file or directory". Stage 6 raises a different `ValueError`, so it slipped
   past and leaked raw internal text.

**Key insight:** the durable-storage abstraction already exists end-to-end in code
(uploads → `input/`, outputs → `outputs/`, prompt logs → `prompt_logs/`). It is simply
**not enabled in the cluster**, and **resume never reads from it** (only from local disk).

---

## 2. The durability floor

To survive a pod recycle, *something* must be durable:

- **Minimum:** the uploaded CV → a full restart can always recompute everything.
- **Better:** all stage outputs → a per-step retry resumes cheaply from the failed step.

Today neither is durable in EKS (storage = `local`, no PVC).

---

## 3. Architecture options

### Option A — S3-backed durable resume (recommended)
Turn on the storage backend that the code already targets, and add the missing read link.

**Infra (owned by Mahender):**
- Set in `k8s/overlays/{prod,dev}` backend env: `CVICHE_STORAGE_BACKEND=s3`,
  `CVICHE_S3_BUCKET=<bucket>`, `CVICHE_S3_PREFIX=cviche`.
- IRSA service-account role with `s3:GetObject/PutObject/ListBucket` on the bucket.
- Bucket + lifecycle policy for cleanup.

**Code (in-repo, I can do now):**
1. **Rehydrate on resume** — in `_prepare_resume`, when a prior stage's local output is
   missing, fetch it from storage (`get_file(run_id, f"outputs/{basename}")`) and write it
   to the expected local path before registering it in `stage_outputs`.
2. **Self-healing start point** — compute the effective resume step as the *first stage
   whose output is missing after rehydration* (capped at the requested step). If only the
   upload survives, this degrades gracefully to a full recompute from stage 1a; it never
   dead-ends at "No input available".
3. **Friendly fallback** — broaden the `is_missing_input` heuristic (`:488`) to also catch
   the "no input available" case, so any residual miss still routes to the
   "Restart with this file" guidance.

**Pros:** code abstraction already exists; also fixes download-after-recycle (#38);
cloud-native; presigned download URLs. **Cons:** needs S3 bucket + IRSA (infra); resume
adds S3 reads.

### Option B — EFS persistent volume (no pipeline code change)
Mount an EFS (RWX) PVC at the outputs + uploads dirs so local files survive recycles and
across replicas. Resume "just works" because the files are still on the (network) disk.

**Pros:** zero pipeline-code change; conceptually simplest for resume. **Cons:** new infra
(EFS CSI driver, StorageClass, PVC); ongoing EFS cost; shared mutable dir; does **not**
fix download URLs / object-storage story; still want the self-healing + friendly-message
code as a robustness layer.

### Option C — storage-first refactor (largest)
Make every stage read its input from and write its output to storage directly; local disk
becomes a scratch cache. Most robust, biggest change, touches every stage's I/O contract.
Not recommended now.

---

## 4. Recommendation

**Option A.** It reuses the storage layer already wired through the code, additionally
fixes the known download-after-recycle bug (#38), and the only new code is the resume-read
link that's currently missing. The self-healing start point makes retry robust even on
partial state, and the friendly-message fallback removes the dead-end error regardless of
backend.

Split of work:
- **I implement now (in-repo, no infra dependency):** §3 code items 1–3. They are safe
  no-ops under `local` storage (nothing to rehydrate) and become fully effective the
  moment S3 is enabled. Within a single pod lifetime (no recycle) item 2 already improves
  resilience today.
- **Mahender wires (infra):** S3 bucket + IRSA + overlay env (§3 infra). This is the
  actual unlock for surviving recycles and also closes #38.

## 5. Note on B2RRRA

Unrecoverable via retry — its intermediate data no longer exists anywhere. Recovery is
"Restart with this file"; if the pod recycled, the upload is also gone (local storage), so
it must be re-uploaded. The plan prevents recurrence; it cannot restore this run.

## 6. Test plan (for the code changes)
- Unit: `_prepare_resume` rehydrates from a stubbed storage backend; self-healing start
  computes the first-missing-output step across representative gap patterns (none missing,
  middle gap, all missing).
- Integration: simulate a recycle by deleting `outputs/` between a completed run and a
  retry, with storage stubbed to hold the outputs → retry resumes from the failed step;
  with storage empty → retry recomputes from stage 1a; with no upload anywhere →
  friendly "Restart / re-upload" message.
