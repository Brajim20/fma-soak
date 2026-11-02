#!/bin/bash
# Host-outage alerts as an issue in this repo, so they arrive by email as soon as they're found (a failed run only
# emails when the whole run ends, hours later). One open issue at a time: a new outage comments on it, and so does
# the recovery. People close it; the bot never does.
#   alert.sh <check output file> <exit code of the check> [issue title] [recovery phrase]
# hosts.py uses the defaults; stuck.py passes "Soak host stuck" "no longer stuck".
set -uo pipefail
out=$1 rc=$2
TITLE=${3:-Soak host offline}
RECOVERED=${4:-back online}
num=$(gh issue list --state open --search "\"$TITLE\" in:title" --json number --jq '.[0].number // empty')
run="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
if [[ $rc != 0 ]]; then
  body=$(printf '%s\n\n%s\n\nRun: %s' "$(grep '^::error::' "$out" | sed 's/^::error:://')" "$(grep -v '^::error::' "$out")" "$run")
  if [[ -n $num ]]; then gh issue comment "$num" --body "$body"
  else gh issue create --title "$TITLE" --body "$body"; fi
elif [[ -n $num ]] && grep -q "$RECOVERED" "$out"; then
  gh issue comment "$num" --body "$(printf '%s\n\nRun: %s' "$(grep "$RECOVERED" "$out")" "$run")"
fi
exit 0
