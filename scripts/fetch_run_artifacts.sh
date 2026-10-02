#!/usr/bin/env bash
# Fetch a run's pod-local pipeline artifacts from cviche-dev into ./analysis/<uid>/.
# Stage JSON lives on whichever pod ran the pipeline -- a backend replica, or in
# queue mode a cviche-worker / cviche-worker-flex pod; the uploaded docx may sit
# on a different pod, so both are searched independently. Pods are wiped on
# every redeploy; S3 keeps the durable copy (see the not-found message).
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $(basename "$0") <document_uid>   (e.g. 89HQVQ)" >&2
    exit 1
fi
doc_uid="$1"

# Anchor to the repo root so ./analysis/ always lands on the gitignored /analysis/.
cd "$(git rev-parse --show-toplevel)"

ns="cviche-dev"
outputs_dir="/app/src/unified_pipeline/outputs"
uploads_dir="/app/web_interface/uploads"

pods=$(kubectl get pods -n "$ns" -l 'app in (cviche-backend,cviche-worker,cviche-worker-flex)' -o name)
[ -n "$pods" ] || { echo "error: no backend or worker pods found in namespace $ns" >&2; exit 1; }

run_pod=""
for pod in $pods; do
    pod="${pod#pod/}"
    if kubectl exec -n "$ns" "$pod" -- \
            ls "$outputs_dir/stage_2_entry_extraction/" 2>/dev/null | grep -q "^${doc_uid}_"; then
        run_pod="$pod"
        break
    fi
done
if [ -z "$run_pod" ]; then
    echo "error: uid $doc_uid not found on any backend or worker pod (wiped by a redeploy?)" >&2
    echo "the durable copy is in S3 (flat outputs/, not stage_* dirs):" >&2
    echo "  aws s3 sync s3://wcm-cviche-storage/cviche/runs/<run_id>/ analysis/$doc_uid/" >&2
    exit 1
fi
echo "stage outputs found on pod $run_pod"

dest="analysis/$doc_uid"
mkdir -p "$dest" "$dest/uploads"

kubectl exec -n "$ns" "$run_pod" -- \
    sh -c "cd $outputs_dir && find . -maxdepth 2 -path './stage_*/${doc_uid}*' | tar -cf - -T -" \
    | tar -xf - -C "$dest"

for pod in $pods; do
    pod="${pod#pod/}"
    upload=$(kubectl exec -n "$ns" "$pod" -- \
        sh -c "ls $uploads_dir/${doc_uid}*.docx 2>/dev/null" || true)
    if [ -n "$upload" ]; then
        for f in $upload; do
            kubectl cp -n "$ns" "$pod:$f" "$dest/uploads/$(basename "$f")"
        done
        echo "upload copied from pod $pod"
        break
    fi
done

echo "artifacts in $dest:"
find "$dest" -type f | sort
