#!/bin/bash
# Host-outage alerts as an issue in this repo, so they arrive by email as soon as they're found (a failed run only
# emails when the whole run ends, hours later). One open issue at a time: a new outage comments on it, and so does
# the recovery. People close it; the bot never does.
#   alert.sh <hosts.py output file> <exit code of hosts.py>
set -uo pipefail
out=$1 rc=$2
TITLE="Soak host offline"
num=$(gh issue list --state open --search "\"$TITLE\" in:title" --json number --jq '.[0].number // empty')
run="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
if [[ $rc != 0 ]]; then
  body=$(printf '%s\n\n%s\n\nRun: %s' "$(grep '^::error::' "$out" | sed 's/^::error:://')" "$(grep -v '^::error::' "$out")" "$run")
  if [[ -n $num ]]; then gh issue comment "$num" --body "$body"
  else gh issue create --title "$TITLE" --body "$body"; fi
elif [[ -n $num ]] && grep -q "back online" "$out"; then
  gh issue comment "$num" --body "$(printf '%s\n\nRun: %s' "$(grep 'back online' "$out")" "$run")"
fi
exit 0
