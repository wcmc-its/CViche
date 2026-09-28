# Production Secrets Provisioning

How each secret CViche needs in production gets into the running container, with concrete patterns for compose-on-EC2 and EKS. Companion to [PRODUCTION_TLS.md](PRODUCTION_TLS.md).

`auth_config.yaml` provisioning is covered separately in the root [README](../README.md) (look for the *Production: provisioning auth_config.yaml* section landed by PR #13). This doc covers everything else.

## The inventory

| Secret | Used by | Sensitivity |
|---|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` (+ `MIGRATE_USER` for the alembic init container) | `app/database.py` via `app/database_factory.py:create_cviche_engine` | High — identifies the production database; under IAM auth no password travels with these |
| `CVICHE_SESSION_SECRET` | session middleware (`itsdangerous`) | High — anyone with this can forge a session cookie |
| `OPENAI_API_KEY` | `src/unified_pipeline/llm_client.py` | High — billing impact |
| AWS credentials | `app/storage/s3_storage.py`, Bedrock client | High — broader blast radius if leaked |
| `CVICHE_S3_BUCKET` | `app/storage/s3_storage.py` | Low (name, not a credential) |
| `CVICHE_ALLOWED_ORIGINS` | CORS + CSRF | Low (config, not a credential) |
| `NCBI_API_KEY` | `src/unified_pipeline/pubmed_*` | Low — rate-limit-only key |

The rule of thumb: anything in the "High" rows must never appear in CI logs, `docker compose config` output you screenshot, the image (image layers are inspectable), or any committed file (including `.env.example`).

## Pattern 1: docker-compose on a VM

Single `.env` file in the same directory as `docker-compose.yml`, mode `0600`, owned by the deploy user.

```bash
# /etc/cviche/.env  (or alongside the compose file)
DB_HOST=cviche-prod.cluster-xxx.us-east-1.rds.amazonaws.com
DB_PORT=3306
DB_NAME=cviche
DB_USER=cviche_app
MIGRATE_USER=cviche_migrate
# No DB password: create_cviche_engine (database_factory.py) mints an RDS IAM
# auth token per connection. DB_AUTH_MODE=password + DB_PASSWORD are for the
# local docker-compose stack only.
CVICHE_SESSION_SECRET=<64-hex-chars-from-secrets.token_hex(32)>
CVICHE_ALLOWED_ORIGINS=https://cviche.med.cornell.edu
CVICHE_S3_BUCKET=wcm-cviche-storage
OPENAI_API_KEY=sk-...
# AWS creds via instance profile, NOT here
```

`docker-compose.prod.yml` reads all of these via `${VAR}` interpolation (`DB_HOST`, `DB_NAME`, `DB_USER` and `MIGRATE_USER` are required, and compose refuses to start without them) and pins `DB_AUTH_MODE=iam`, so `docker compose --env-file /etc/cviche/.env -f docker-compose.yml -f docker-compose.prod.yml up -d` picks them up. Variables exported in the shell take precedence over `--env-file`.

**Provisioning options:**
- Fetch the file from AWS Secrets Manager on host boot (cloud-init / Ansible). Don't bake into the AMI.
- Use SSM Parameter Store with `aws ssm get-parameter --with-decryption` in a systemd `EnvironmentFile=` unit.

**Hard rules:**
- `.env` must be in `.gitignore` (it already is at the repo level — verify the deploy user doesn't have a private fork that committed it).
- Never write `.env` to a world-readable path. `chmod 600` and chown to the docker daemon's user.
- Never `docker compose config` and paste the output anywhere — it expands `${VAR}` references and dumps every secret to stdout.

## Pattern 2: EKS

### Env-var-sourced secrets

Use [External Secrets Operator](https://external-secrets.io/) backed by AWS Secrets Manager. Create one `ExternalSecret` per env-var group:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: cviche-app-secrets
  namespace: cviche
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: cviche-secret-store      # ClusterSecretStore wired to Secrets Manager
    kind: ClusterSecretStore
  target:
    name: cviche-app-secrets       # creates k8s Secret of the same name
    creationPolicy: Owner
  data:
    - secretKey: DB_HOST
      remoteRef:
        key: cviche/prod/db_host
    - secretKey: DB_PORT
      remoteRef:
        key: cviche/prod/db_port
    - secretKey: DB_NAME
      remoteRef:
        key: cviche/prod/db_name
    - secretKey: DB_USER
      remoteRef:
        key: cviche/prod/db_user
    - secretKey: MIGRATE_USER
      remoteRef:
        key: cviche/prod/db_migrate_user
    - secretKey: CVICHE_SESSION_SECRET
      remoteRef:
        key: cviche/prod/session_secret
    - secretKey: OPENAI_API_KEY
      remoteRef:
        key: cviche/prod/openai_api_key
```

The backend Deployment then references it:

```yaml
spec:
  template:
    spec:
      serviceAccountName: cviche-backend   # see IRSA below
      containers:
        - name: backend
          envFrom:
            - secretRef:
                name: cviche-app-secrets   # populated by the ExternalSecret above
          env:
            - name: CVICHE_S3_BUCKET
              value: wcm-cviche-storage    # not a secret; plain Deployment env
            - name: CVICHE_ALLOWED_ORIGINS
              value: https://cviche.med.cornell.edu
            - name: CVICHE_STORAGE_BACKEND
              value: s3
            - name: CVICHE_SECURE_COOKIES
              value: "true"
            - name: CVICHE_LOG_FORMAT
              value: "json"
```

If External Secrets Operator isn't an option, the fallback is to provision `Secret` objects directly via a controlled pipeline that pulls from Secrets Manager out-of-band. Avoid `kubectl create secret --from-literal` in interactive shells; the value lands in shell history.

### AWS credentials: IRSA, not static keys

`app/storage/s3_storage.py:6` says "Authentication uses the boto3 default credential chain (IRSA on EKS)." Make that real by mapping the pod's ServiceAccount to an IAM role with an OIDC trust policy.

#### IAM policy attached to the role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectAcl"
      ],
      "Resource": "arn:aws:s3:::wcm-cviche-storage/cviche/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::wcm-cviche-storage",
      "Condition": {
        "StringLike": {"s3:prefix": ["cviche/*", "cviche"]}
      }
    }
  ]
}
```

#### Trust policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<account-id>:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/<cluster-oidc-id>"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.us-east-1.amazonaws.com/id/<cluster-oidc-id>:sub":
            "system:serviceaccount:cviche:cviche-backend",
          "oidc.eks.us-east-1.amazonaws.com/id/<cluster-oidc-id>:aud":
            "sts.amazonaws.com"
        }
      }
    }
  ]
}
```

#### ServiceAccount annotation

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cviche-backend
  namespace: cviche
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<account-id>:role/cviche-backend-prod
```

When the Deployment sets `serviceAccountName: cviche-backend`, the EKS pod-identity webhook injects `AWS_ROLE_ARN` and `AWS_WEB_IDENTITY_TOKEN_FILE` into the container. boto3 picks them up via the default credential chain. **No static `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` should be set anywhere in the prod manifests.**

### If you're using Bedrock as the LLM provider

Same IRSA role, add the Bedrock permissions:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream" 
  ],
  "Resource": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6:0"
}
```

Add other model ARNs as needed. Limit to specific model ARNs; do NOT grant `Resource: "*"`.

## Bucket policy

The S3 bucket itself should:
- Have `BlockPublicAcls` and `IgnorePublicAcls` set on the public-access block.
- Have a bucket policy that denies any non-TLS request (`aws:SecureTransport: false` → Deny).
- Enable server-side encryption (SSE-S3 is fine; SSE-KMS if you need audit on every fetch).
- Enable versioning so a pipeline bug that overwrites a run's artifacts can be rolled back.
- Have a lifecycle policy that transitions `cviche/runs/*/input/` to GLACIER after 90 days (input CVs are read-once after the pipeline completes).

## Anti-patterns

- ❌ `docker run -e CVICHE_SESSION_SECRET=...` in any shell that has history enabled.
- ❌ Committing `.env` (the gitignore catches this; double-check after merging branches with renamed files).
- ❌ Passing secrets as command-line arguments (visible in `ps`).
- ❌ Setting `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` as plain env vars on the backend Deployment — defeats the IRSA setup and rotation becomes manual.
- ❌ Using one `CVICHE_S3_BUCKET` across `dev` and `prod` with the same prefix. Use the `CVICHE_S3_PREFIX` env var to namespace.
- ❌ Rotating `CVICHE_SESSION_SECRET` in place without a graceful transition — every existing session cookie becomes invalid. If rotation is needed, plan a forced re-login window or implement a multi-key signer.

## Verification checklist before go-live

- [ ] `kubectl get secret cviche-app-secrets -o yaml -n cviche` shows base64'd values for every expected key — and only those keys.
- [ ] `kubectl exec -n cviche <backend-pod> -- printenv | grep -E '(CVICHE_|AWS_|OPENAI)' | sort` shows every expected env var present, none extra.
- [ ] `kubectl exec -n cviche <backend-pod> -- env | grep AWS_ACCESS_KEY_ID` returns nothing (IRSA, not static keys).
- [ ] `kubectl exec -n cviche <backend-pod> -- env | grep AWS_ROLE_ARN` shows the expected role ARN.
- [ ] `aws s3 ls s3://<bucket>/<prefix>/` from inside the pod succeeds; the same from a pod in another namespace fails.
- [ ] Bucket policy denies plain-HTTP requests; verify with `curl http://<bucket>.s3.amazonaws.com/...` returning 403.
- [ ] `aws s3api get-bucket-versioning --bucket <bucket>` returns `Enabled`.
- [ ] No grep for `aws_access_key_id`, `aws_secret_access_key`, `OPENAI_API_KEY=sk-` in any committed file (`git grep -i`). The .env.example only shows placeholders.
