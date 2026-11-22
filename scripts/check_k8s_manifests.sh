#!/usr/bin/env bash
# Render both kustomize overlays, validate what they render against the
# Kubernetes API schemas, and assert two #692 hardening settings on the render.
# Before this, the only render check was
# buildspec.yaml's `kubectl kustomize ... > /dev/null`, which runs at deploy
# time, after the merge: a broken patch or a misspelled field merged to dev
# green. ci.yml's k8s-manifests job runs this script; local_ci.sh runs it too.
#
# The schema check is structural only: a field that is valid but missing (the
# ssl-policy annotation, the pod's fsGroup) passes it. So the policy check
# below reads each overlay's render and fails when:
#   - no Ingress is rendered, or an Ingress lacks
#     alb.ingress.kubernetes.io/ssl-policy=$SSL_POLICY;
#   - no Deployment mounts the SAML secret;
#   - a Deployment mounts it world-readable (no defaultMode, or a mode with the
#     world-read bit set);
#   - a Deployment mounts it without world-read but its pod has no
#     securityContext.fsGroup $APP_GID, so uid 1000 can't read its own key.
#
# usage: scripts/check_k8s_manifests.sh
# Needs kustomize and kubeconform on PATH, or KUSTOMIZE / KUBECONFORM set to
# their paths, and python3 with PyYAML (pinned in requirements.txt). ci.yml
# installs the pinned versions (kustomize v5.8.3, kubeconform v0.8.0,
# pyyaml 6.0.3). A missing tool fails the check; it never reads as a pass.
#
# kubeconform fetches the schemas for K8S_VERSION over the network.
set -euo pipefail

# The EKS control-plane version the overlays deploy to. `aws eks
# describe-cluster --name reciter` read 1.37 on 2026-11-22. Bump it with the
# cluster, so a field the cluster dropped is caught here and not at apply.
K8S_VERSION=1.37.0
# kubeconform's default schema source is that repo's master branch. Pinning a
# commit means the same manifests always get the same verdict.
SCHEMA_COMMIT=e0c72a1bc5f409d1685c0e1323460c8a98cd7459  # 2026-10-31
SCHEMA_LOCATION="https://raw.githubusercontent.com/yannh/kubernetes-json-schema/$SCHEMA_COMMIT/{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json"
OVERLAYS=(k8s/overlays/dev k8s/overlays/prod)
# The ALB TLS policy k8s/base/ingress.yaml pins: TLS 1.2/1.3 only.
SSL_POLICY=ELBSecurityPolicy-TLS13-1-2-2021-06
# The backend image's app gid (web_interface/backend/Dockerfile APP_GID).
APP_GID=1000
# A prefix: kustomize's secretGenerator appends a content hash to the name.
SAML_SECRET_PREFIX=cviche-saml-certs

KUSTOMIZE="${KUSTOMIZE:-kustomize}"
KUBECONFORM="${KUBECONFORM:-kubeconform}"
for tool in "$KUSTOMIZE" "$KUBECONFORM"; do
  if ! command -v "$tool" > /dev/null 2>&1; then
    echo "check_k8s_manifests.sh: '$tool' not found. Install it or set KUSTOMIZE/KUBECONFORM." >&2
    exit 1
  fi
done
if ! python3 -c 'import yaml' > /dev/null 2>&1; then
  echo "check_k8s_manifests.sh: python3 with PyYAML not found (pip install pyyaml==6.0.3)." >&2
  exit 1
fi
echo "kustomize $("$KUSTOMIZE" version), kubeconform $("$KUBECONFORM" -v), schemas for Kubernetes $K8S_VERSION"

# Reads one overlay's render on stdin; prints each violation, exits 1 on any.
read -r -d '' POLICY_PY << 'PY' || true
import os
import sys

import yaml

SSL_POLICY_KEY = "alb.ingress.kubernetes.io/ssl-policy"
SECRET_DEFAULT_MODE = 0o644  # the kubelet's mode when defaultMode is unset
WORLD_READ = 0o004
ssl_policy = os.environ["SSL_POLICY"]
app_gid = int(os.environ["APP_GID"])
saml_prefix = os.environ["SAML_SECRET_PREFIX"]

docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
errors = []
ingresses = [d for d in docs if d["kind"] == "Ingress"]
if not ingresses:
    errors.append("no Ingress rendered")
for ing in ingresses:
    got = (ing["metadata"].get("annotations") or {}).get(SSL_POLICY_KEY)
    if got != ssl_policy:
        errors.append(f"Ingress {ing['metadata']['name']}: {SSL_POLICY_KEY} is {got!r}, want {ssl_policy!r}")
saml_volumes = 0
for dep in (d for d in docs if d["kind"] == "Deployment"):
    pod = dep["spec"]["template"]["spec"]
    where = f"Deployment {dep['metadata']['name']}"
    for vol in pod.get("volumes") or []:
        secret = vol.get("secret") or {}
        if not secret.get("secretName", "").startswith(saml_prefix):
            continue
        saml_volumes += 1
        mode = secret.get("defaultMode", SECRET_DEFAULT_MODE)
        if mode & WORLD_READ:
            errors.append(f"{where}: volume {vol['name']} mounts the SAML secret world-readable (defaultMode {mode:#o})")
        elif (pod.get("securityContext") or {}).get("fsGroup") != app_gid:
            errors.append(f"{where}: volume {vol['name']} has defaultMode {mode:#o} but the pod has no securityContext.fsGroup {app_gid}, so uid {app_gid} can't read the key")
if not saml_volumes:
    errors.append(f"no Deployment mounts a {saml_prefix}* secret")
for e in errors:
    print(f"policy: {e}", file=sys.stderr)
if errors:
    sys.exit(1)
print(f"policy: OK ({len(ingresses)} Ingress, {saml_volumes} SAML secret volume)")
PY
export SSL_POLICY APP_GID SAML_SECRET_PREFIX

cd "$(dirname "${BASH_SOURCE[0]}")/.."
failed=0
for overlay in "${OVERLAYS[@]}"; do
  echo "== $overlay"
  if ! rendered=$("$KUSTOMIZE" build "$overlay"); then
    echo "FAILED: $overlay (kustomize build)" >&2
    failed=1
    continue
  fi
  # -strict rejects a field the schema doesn't define (a typo, or a field
  # nested at the wrong level). -ignore-missing-schemas lets a custom resource
  # through, since no CRD schema is published for it; the summary line counts
  # what was skipped, so a skip stays visible. Both overlays, and both checks,
  # run even when one fails, so one run reports every failure.
  if ! "$KUBECONFORM" -strict -ignore-missing-schemas -summary \
      -kubernetes-version "$K8S_VERSION" -schema-location "$SCHEMA_LOCATION" - <<< "$rendered"; then
    echo "FAILED: $overlay (schema)" >&2
    failed=1
  fi
  if ! python3 -c "$POLICY_PY" <<< "$rendered"; then
    echo "FAILED: $overlay (policy)" >&2
    failed=1
  fi
done
exit "$failed"
