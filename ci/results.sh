#!/bin/bash
# State for CI runs lives on the `results` branch, never on main.
#   results.sh restore         copy the branch's results/ into the harness checkout
#   results.sh save "<msg>"    put this run's results/ on top of the latest branch and push
# Both platform jobs save into the same branch hours after they restored it. Their files don't overlap (per-host
# state, per-platform decks and day files, per-record files), so copying a job's files onto the newest branch
# merges cleanly, as long as a job copies only what it changed: restore records a checksum of every file, and
# save skips files that still match (a job's restored copy of the other platform's files is hours stale).
# The shared ledger and digest are rebuilt by the report job after both have saved.
# Screenshots stay out (run artifacts only).
set -euo pipefail
cd "$(dirname "$0")/.."
BR=results
SUMS=${RUNNER_TEMP:-/tmp}/fma-soak-restored.sums
sums() { find results -type f -not -path '*/evidence/*' -print0 | sort -z | xargs -0 -r shasum -a 256; }
case "$1" in
  restore)
    if git fetch -q origin "$BR" 2>/dev/null; then
      git archive FETCH_HEAD results | tar -x
      echo "restored results/ from $BR ($(git rev-parse --short FETCH_HEAD))"
      sums > "$SUMS"
    else
      echo "no $BR branch yet: starting fresh"; : > "$SUMS"
    fi
    ;;
  save)
    [[ -d results ]] || { echo "nothing to save"; exit 0; }
    git config user.name "fma-soak bot"; git config user.email "fma-soak@users.noreply.github.com"
    OUT=$(mktemp -d)
    touch "$SUMS"
    sums | grep -vxFf "$SUMS" | sed 's/^[0-9a-f]*  //' > "$OUT/changed" || true   # new or changed since restore
    echo "$(wc -l < "$OUT/changed" | tr -d ' ') changed files"
    for i in 1 2 3 4 5; do
      rm -rf "$OUT/w"; git worktree prune
      if git fetch -q origin "$BR" 2>/dev/null; then git worktree add -q --detach "$OUT/w" FETCH_HEAD
      else git worktree add -q --detach "$OUT/w" && git -C "$OUT/w" checkout -q --orphan tmp && git -C "$OUT/w" rm -rqf . ; fi
      rsync -a --ignore-times --files-from="$OUT/changed" ./ "$OUT/w/"   # same size + mtime would be skipped
      git -C "$OUT/w" add -A results
      git -C "$OUT/w" diff --cached --quiet && { echo "no changes"; exit 0; }
      git -C "$OUT/w" commit -qm "$2"
      git -C "$OUT/w" push -q origin "HEAD:refs/heads/$BR" && { echo "saved to $BR"; exit 0; }
      sleep $((RANDOM % 20 + 5))   # the other job pushed first: redo on top of it
    done
    exit 1
    ;;
  *) echo "usage: $0 restore|save <msg>"; exit 2 ;;
esac
