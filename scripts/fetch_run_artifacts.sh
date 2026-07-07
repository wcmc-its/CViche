#!/usr/bin/env bash
# Fetch a run's pod-local pipeline artifacts from cviche-dev into ./analysis/<uid>/.
# Stage JSON lives on whichever backend replica ran the pipeline; the uploaded
# docx may sit on a different replica, so both are searched independently.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $(basename "$0") <document_uid>   (e.g. 89HQVQ)" >&2
    exit 1
fi
doc_uid="$1"

# Anchor to the repo root so ./analysis/ always lands on the gitignored /analysis/.
cd "$(git rev-parse --show-toplevel)"

ns="cviche-dev"
container="backend"
outputs_dir="/app/src/unified_pipeline/outputs"
uploads_dir="/app/web_interface/uploads"

pods=$(kubectl get pods -n "$ns" -l app=cviche-backend -o name)
if [ -z "$pods" ]; then
    pods=$(kubectl get pods -n "$ns" -o name | grep backend || true)
fi
[ -n "$pods" ] || { echo "error: no backend pods found in namespace $ns" >&2; exit 1; }

run_pod=""
for pod in $pods; do
    pod="${pod#pod/}"
    if kubectl exec -n "$ns" -c "$container" "$pod" -- \
            ls "$outputs_dir/stage_2_entry_extraction/" 2>/dev/null | grep -q "^${doc_uid}_"; then
        run_pod="$pod"
        break
    fi
done
[ -n "$run_pod" ] || { echo "error: uid $doc_uid not found on any backend replica ($pods)" >&2; exit 1; }
echo "stage outputs found on pod $run_pod"

dest="analysis/$doc_uid"
mkdir -p "$dest" "$dest/uploads"

kubectl exec -n "$ns" -c "$container" "$run_pod" -- \
    sh -c "cd $outputs_dir && find . -maxdepth 2 -path './stage_*/${doc_uid}*' | tar -cf - -T -" \
    | tar -xf - -C "$dest"

for pod in $pods; do
    pod="${pod#pod/}"
    upload=$(kubectl exec -n "$ns" -c "$container" "$pod" -- \
        sh -c "ls $uploads_dir/${doc_uid}*.docx 2>/dev/null" || true)
    if [ -n "$upload" ]; then
        for f in $upload; do
            kubectl cp -n "$ns" -c "$container" "$pod:$f" "$dest/uploads/$(basename "$f")"
        done
        echo "upload copied from pod $pod"
        break
    fi
done

echo "artifacts in $dest:"
find "$dest" -type f | sort
