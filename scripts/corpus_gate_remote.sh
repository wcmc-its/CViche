#!/usr/bin/env bash
# Run the render/doctor corpus gate (#2176) on one PR, in CodeBuild, from your own machine.
#
#   scripts/corpus_gate_remote.sh <PR number> [--i-read-the-diff] [--comment]
#
# The build runs the PR's code with read access to real CVs (the gate farm),
# so this refuses to start it until you confirm you have read the PR's diff:
# type the PR number at the prompt, or pass --i-read-the-diff. It waits for
# the build and prints the masked report render_gate_controls.py wrote (counts,
# lint names, manifest or hashed uids; nothing from a CV). --comment also posts
# that report on the PR.
#
# Needs gh, and AWS credentials that may codebuild:StartBuild on the project
# and read its CloudWatch log. The project and its buildspec are set up by
# hand: docs/guides/render-doctor-gates.md, "AWS setup (Paul)".
# CORPUS_GATE_PROJECT names the project (default cviche-corpus-gate).
set -euo pipefail

repo=wcmc-its/CViche
project=${CORPUS_GATE_PROJECT:-cviche-corpus-gate}
usage="usage: $(basename "$0") <PR number> [--i-read-the-diff] [--comment]"

pr="" read_diff="" comment=""
for arg in "$@"; do
    case $arg in
        --i-read-the-diff) read_diff=1 ;;
        --comment) comment=1 ;;
        *[!0-9]* | "") echo "$usage" >&2; exit 2 ;;
        *) pr=$arg ;;
    esac
done
[ -n "$pr" ] || { echo "$usage" >&2; exit 2; }

head_sha=$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid)
base_ref=$(gh pr view "$pr" --repo "$repo" --json baseRefName --jq .baseRefName)
base_sha=$(gh api "repos/$repo/compare/$base_ref...$head_sha" --jq .merge_base_commit.sha)

echo "PR #$pr: head $head_sha, merge base $base_sha (on $base_ref). Files changed:"
gh pr diff "$pr" --repo "$repo" --name-only | sed 's/^/  /'
if [ -z "$read_diff" ]; then
    echo "This runs that code with read access to real CVs."
    printf 'Type the PR number to confirm you have read its whole diff at %s: ' "${head_sha:0:8}"
    read -r typed
    [ "$typed" = "$pr" ] || { echo "Not confirmed; no build started." >&2; exit 1; }
fi

build_id=$(aws codebuild start-build --project-name "$project" --source-version "$head_sha" \
    --environment-variables-override \
        "name=HEAD_SHA,value=$head_sha,type=PLAINTEXT" \
        "name=BASE_SHA,value=$base_sha,type=PLAINTEXT" \
        "name=PR_NUMBER,value=$pr,type=PLAINTEXT" \
    --query build.id --output text)
echo "Started $build_id; waiting."

build() { aws codebuild batch-get-builds --ids "$build_id" --query "builds[0].$1" --output text; }
while [ "$(build buildStatus)" = IN_PROGRESS ]; do sleep 30; done
status=$(build buildStatus)
# The gate step never fails the build (report-only), so anything but SUCCEEDED
# is a setup step that refused or broke: a moved PR head, a base not on dev.
[ "$status" = SUCCEEDED ] || { echo "Build $status: no report. Read the build log in the console." >&2; exit 1; }

# The buildspec logs the report as one base64 line after the run; CloudWatch
# can lag the build by a few seconds.
read -r group stream < <(build 'logs.[groupName,streamName]')
log="" encoded=""
for _ in 1 2 3 4 5 6; do
    log=$(aws logs get-log-events --log-group-name "$group" --log-stream-name "$stream" \
        --limit 500 --query 'events[*].[message]' --output text)
    encoded=$(printf '%s\n' "$log" | sed -n 's/^CORPUS_GATE_SUMMARY //p' | tail -n 1)
    [ -n "$encoded" ] && break
    sleep 10
done
[ -n "$encoded" ] || { echo "No report in the build log; read it in the console." >&2; exit 1; }
report=$(printf '%s' "$encoded" | base64 --decode)
printf '%s\n' "$report"
gate_exit=$(printf '%s\n' "$log" | sed -n 's/^CORPUS_GATE_EXIT //p' | tail -n 1)
echo "render_gate_controls.py exit $gate_exit (0 same as base, 1 head differs, 2 no evidence)."

if [ -n "$comment" ]; then
    # shellcheck disable=SC2016  # the backticks are Markdown code spans, not command substitution
    printf 'Corpus gate run by hand (CodeBuild `%s`), head `%s`, base `%s`.\n\n%s\n' \
        "${build_id#*:}" "${head_sha:0:8}" "${base_sha:0:8}" "$report" |
        gh pr comment "$pr" --repo "$repo" --body-file -
fi
