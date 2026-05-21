# Production Backups & Data Durability

What CViche relies on for durability in production, what to verify before go-live, and how to restore from a snapshot. Companion to [PRODUCTION_TLS.md](PRODUCTION_TLS.md) and [PRODUCTION_SECRETS.md](PRODUCTION_SECRETS.md).

## What's at risk if backups are wrong

| Data | Where it lives | What's lost on failure |
|---|---|---|
| Run state (runs, steps, logs, llm_usage, feedback) | RDS MariaDB | Every run's history, cost ledger, feedback audit trail |
| Uploaded CVs + per-step JSON + WCM docx output | S3 (`CVICHE_S3_BUCKET`) | Originals + intermediates + final output |
| Per-LLM-call prompt + response transcripts | Container filesystem (see below) | All audit and cost-attribution material |
| User accounts, consent records, system config | RDS MariaDB | Returning users can't log in, consent has to be re-collected |

The first three each need a separate durability story.

## 1. RDS MariaDB

Prod uses managed MariaDB (`$CVICHE_USER@$CVICHE_HOST:3306/cviche`). Confirmed schema-imported on 2026-05-16 (see project memory).

### Expected configuration

| Setting | Value | Why |
|---|---|---|
| Automated backups | Enabled | One nightly snapshot retained automatically |
| Backup retention | **30 days** | RDS default is 7; CViche's compliance / audit posture wants 30 |
| Point-in-Time Recovery (PITR) | Enabled | Recover to any second within retention; covers "oops I just ran `DELETE` without WHERE" |
| Backup window | Off-peak (e.g., 06:00–07:00 UTC) | Backup-induced IO does not collide with user activity |
| Storage encryption (KMS) | Enabled | Snapshots inherit; restore-as-snapshot still encrypted |
| Multi-AZ | Enabled (prod tier) | Synchronous standby; takes over within minutes on primary failure |
| Final snapshot on delete | Required | A `DeleteDBInstance` without it loses everything |
| Deletion protection | Enabled | Block accidental `DeleteDBInstance` outright |

### Verification commands

Run these with the WCM AWS profile that has `rds:DescribeDBInstances` permission:

```bash
aws rds describe-db-instances --db-instance-identifier cviche-prod \
  --query 'DBInstances[0].{
      Retention: BackupRetentionPeriod,
      PITR: BackupTarget,
      Window: PreferredBackupWindow,
      Encryption: StorageEncrypted,
      MultiAZ: MultiAZ,
      DeletionProtection: DeletionProtection
  }'

# Confirm the latest automated snapshot exists and is recent:
aws rds describe-db-snapshots --db-instance-identifier cviche-prod \
  --snapshot-type automated \
  --query 'DBSnapshots[?Status==`available`] | sort_by(@, &SnapshotCreateTime) | [-1]'
```

Expected output: `Retention: 30`, `PITR: <target>`, `DeletionProtection: true`, latest automated snapshot within the last 24 hours.

### Restore runbook

**Goal:** Restore the prod database to a temporary instance, validate, then promote (or copy data back) in under 30 minutes.

1. **Identify the target.**
   - PITR target time (e.g., "5 minutes before the bad DELETE landed at 14:32 UTC") → use **option A** below.
   - Latest snapshot → use **option B**.

2. **Option A — Point-in-Time restore:**
   ```bash
   aws rds restore-db-instance-to-point-in-time \
     --source-db-instance-identifier cviche-prod \
     --target-db-instance-identifier cviche-restore-$(date +%Y%m%d-%H%M) \
     --restore-time 2026-XX-XXTHH:MM:SSZ \
     --db-subnet-group-name <subnet-group> \
     --vpc-security-group-ids <sg-id> \
     --no-publicly-accessible
   ```

3. **Option B — Restore from a specific snapshot:**
   ```bash
   aws rds describe-db-snapshots --db-instance-identifier cviche-prod \
     --query 'DBSnapshots[?Status==`available`].[DBSnapshotIdentifier,SnapshotCreateTime]' \
     --output table
   aws rds restore-db-instance-from-db-snapshot \
     --source-db-snapshot-identifier <snapshot-id> \
     --db-instance-identifier cviche-restore-$(date +%Y%m%d-%H%M) \
     --db-subnet-group-name <subnet-group> \
     --vpc-security-group-ids <sg-id> \
     --no-publicly-accessible
   ```

4. **Wait for `available` status** (~10 minutes for a small instance):
   ```bash
   aws rds describe-db-instances \
     --db-instance-identifier cviche-restore-... \
     --query 'DBInstances[0].DBInstanceStatus'
   ```

5. **Validate the restored data.** Point a local mariadb client at it (via bastion / SSM) and spot-check:
   ```sql
   SELECT MAX(created_at) FROM runs;          -- last run before incident?
   SELECT COUNT(*) FROM users WHERE active;   -- expected user count?
   SELECT COUNT(*) FROM consent WHERE accepted_at > '2026-01-01';
   ```

6. **Promote.** Two strategies:

   - **Cut over (fastest):** Update the production deployment's `CVICHE_DATABASE_URL` to point at the restore instance, roll a new deployment. The old instance stays around as a frozen archive. Renames are cheap (`aws rds modify-db-instance --db-instance-identifier ... --new-db-instance-identifier cviche-prod-old --apply-immediately`, then rename the restore to `cviche-prod`).
   - **Copy-back (safer):** Dump from the restore instance, import into a fresh prod (or use `mysqldump | mysql` for table-level recovery). Slower but lets you keep the live instance unchanged.

7. **Delete the temporary restore** once the issue is resolved (skip if you cut over):
   ```bash
   aws rds delete-db-instance \
     --db-instance-identifier cviche-restore-... \
     --skip-final-snapshot
   ```

**Drill expectation:** Step 1–5 should complete in under 30 minutes for a sub-50GB instance. Document the actual time in the post-drill notes.

## 2. S3 (run artifacts)

`CVICHE_S3_BUCKET` holds the durable copies of every run's input/intermediates/output. Settings to verify (a subset of [PRODUCTION_SECRETS.md](PRODUCTION_SECRETS.md)'s bucket-policy section, repeated here so durability is in one doc):

```bash
aws s3api get-bucket-versioning --bucket <bucket>
# Status: Enabled

aws s3api get-bucket-encryption --bucket <bucket>
# ServerSideEncryptionConfiguration applied

aws s3api get-bucket-lifecycle-configuration --bucket <bucket>
# Rule transitioning cviche/runs/*/input/ -> GLACIER after 90 days
```

**Restore from S3 versioning:** any object overwritten can be retrieved with:
```bash
aws s3api list-object-versions --bucket <bucket> --prefix cviche/runs/<run-id>/
aws s3api get-object --bucket <bucket> --key <key> --version-id <version-id> <local-file>
```

Cross-region replication is not currently in scope — RPO is dominated by the RDS retention window, not S3 durability (11 nines).

## 3. Prompt logs (currently lost on container restart)

**Current state — fragile.** The pipeline writes per-LLM-call transcripts to `src/unified_pipeline/prompt_logs/` (see `src/unified_pipeline/core/prompt_logger.py:36`). In dev they're persisted via the named docker volume `backend_prompt_logs` in `web_interface/docker-compose.yml:59`. In prod, `web_interface/docker-compose.prod.yml:22` sets `volumes: []`, so prompt logs live on the container's writable layer and are **lost on every restart, replica scale-up, or redeploy**.

The reader at `web_interface/backend/app/api/steps.py:442` is therefore reading a directory that exists only for the lifetime of the current pod. Two failure modes:

1. The "view prompts" tab on a run silently returns empty for any run whose pod has since been replaced.
2. Cost-attribution / regression-replay audits can only cover the trailing-N-minutes of activity — anything before the last rollout is gone.

**Recommended fix:** persist prompt logs to S3 under `cviche/runs/{run_id}/prompt_logs/`, parallel to the existing `S3RunStorage` pattern. Two implementation options:

- **A — orchestrator uploads at step end** (recommended). The pipeline keeps writing to local fs (no change). The orchestrator, after each step completes, lists the newly-written `prompt_logs/` files and uploads them via the existing `S3RunStorage.put_file` API. The API reader at `steps.py:442` reads from S3 instead of the local fs when `CVICHE_STORAGE_BACKEND=s3`. Pros: no per-LLM-call S3 latency on the critical path; minimal new code; reuses the existing S3 abstraction. Cons: a step that crashes after writing prompts but before the upload loses the in-flight transcripts (acceptable — the step failure is already a higher-order incident).
- **B — prompt_logger writes directly to S3** behind a `PROMPT_LOG_BACKEND={local|s3|disabled}` env var. Synchronous, simpler reader logic, but adds ~100 ms per LLM call (a CV run makes 50–80 LLM calls; cumulative cost is significant).

**Option A is the recommended path.** Tracked as follow-up issue #28 rather than in-scope here, because (a) it needs verification against real prod S3 + IRSA setup, and (b) it touches the orchestrator's step-completion hook which is shared with other refactors in flight (PR #15 Redis broker design).

**Until that lands, treat prompt_logs as ephemeral:**

- Anyone debugging a past prod run should expect "no prompt logs found" if the pod was replaced.
- Consider temporarily increasing replica count to 1 during a cost-audit window so prompt logs accumulate in one place. Not a substitute for the persistence fix.

## Verification checklist before go-live

- [ ] RDS automated backups enabled, retention >= 30 days, PITR enabled
- [ ] RDS Multi-AZ enabled and deletion protection on
- [ ] RDS storage encryption (KMS) on; snapshots inherit
- [ ] Latest automated snapshot is within the last 24 hours
- [ ] **A restore drill has been performed end-to-end** (option A and option B) within the last 90 days; runbook timing matches the doc; results recorded in this directory or a shared ops doc
- [ ] S3 bucket versioning enabled
- [ ] S3 server-side encryption configured
- [ ] S3 lifecycle policy transitions input CVs to GLACIER after 90 days
- [ ] A follow-up issue is open for prompt-log S3 persistence (option A)
