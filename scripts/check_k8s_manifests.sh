#!/usr/bin/env bash
# Render both kustomize overlays and validate what they render against the
# Kubernetes API schemas (#692). Before this, the only render check was
# buildspec.yaml's `kubectl kustomize ... > /dev/null`, which runs at deploy
# time, after the merge: a broken patch or a misspelled field merged to dev
# green. ci.yml's k8s-manifests job runs this script; local_ci.sh runs it too.
#
# usage: scripts/check_k8s_manifests.sh
# Needs kustomize and kubeconform on PATH, or KUSTOMIZE / KUBECONFORM set to
# their paths. ci.yml installs the pinned versions (kustomize v5.8.3,
# kubeconform v0.8.0). A missing tool fails the check; it never reads as a pass.
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

KUSTOMIZE="${KUSTOMIZE:-kustomize}"
KUBECONFORM="${KUBECONFORM:-kubeconform}"
for tool in "$KUSTOMIZE" "$KUBECONFORM"; do
  if ! command -v "$tool" > /dev/null 2>&1; then
    echo "check_k8s_manifests.sh: '$tool' not found. Install it or set KUSTOMIZE/KUBECONFORM." >&2
    exit 1
  fi
done
echo "kustomize $("$KUSTOMIZE" version), kubeconform $("$KUBECONFORM" -v), schemas for Kubernetes $K8S_VERSION"

cd "$(dirname "${BASH_SOURCE[0]}")/.."
failed=0
for overlay in "${OVERLAYS[@]}"; do
  echo "== $overlay"
  # -strict rejects a field the schema doesn't define (a typo, or a field
  # nested at the wrong level). -ignore-missing-schemas lets a custom resource
  # through, since no CRD schema is published for it; the summary line counts
  # what was skipped, so a skip stays visible. Both overlays are checked even
  # when the first fails, so one run reports every broken overlay.
  if ! "$KUSTOMIZE" build "$overlay" \
      | "$KUBECONFORM" -strict -ignore-missing-schemas -summary \
          -kubernetes-version "$K8S_VERSION" -schema-location "$SCHEMA_LOCATION" -; then
    echo "FAILED: $overlay" >&2
    failed=1
  fi
done
exit "$failed"
