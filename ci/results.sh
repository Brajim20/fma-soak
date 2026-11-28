#!/bin/bash
# State for CI runs lives on the `results` branch, never on main.
#   results.sh restore         copy the branch's results/ into the harness checkout
#   results.sh save "<msg>"    put this run's results/ on top of the latest branch and push
#   results.sh checkpoint "<msg>" [minutes]
#                              save every <minutes> (default 15) until killed, so a runner GitHub loses mid-pass
#                              costs at most that much (soak darwin slot 1 lost ~3 h of 860's records 2026-11-26).
#                              The soak job starts it in the background before the pass; the final save stops it.
# Both platform jobs save into the same branch hours after they restored it. Their files don't overlap (per-host
# state, per-platform decks and day files, per-record files), so copying a job's files onto the newest branch
# merges cleanly, as long as a job copies only what it changed: restore records a checksum of every file, and
# save skips files that still match (a job's restored copy of the other platform's files is hours stale).
# The shared ledger and digest are rebuilt by the report job after both have saved.
# Screenshots stay out (run artifacts only).
set -euo pipefail
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
cd "$(dirname "$0")/.."
BR=results
SUMS=${RUNNER_TEMP:-/tmp}/fma-soak-restored.sums
LOCK=${RUNNER_TEMP:-/tmp}/fma-soak-save.lock
PIDF=${RUNNER_TEMP:-/tmp}/fma-soak-checkpoint.pid
sums() { find results -type f -not -path '*/evidence/*' -not -name '*.tmp' -print0 | sort -z | xargs -0 -r shasum -a 256; }
# one save at a time per job: the checkpoint loop and the final save share the worktree dance below
lock() {
  for _ in $(seq 120); do
    mkdir "$LOCK" 2>/dev/null && { echo $$ > "$LOCK/pid"; return 0; }
    kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null || rm -rf "$LOCK"   # its holder was killed mid-save
    sleep 5
  done
  echo "save lock held for 10 min: giving up"; return 1
}
unlock() { rm -rf "$LOCK"; }
# drop JSON files that don't parse: a checkpoint can land while a writer is halfway through one, and a runner lost
# mid-write leaves a truncated file. The next save picks the file up once it's whole.
whole_json() {
  python3 -c '
import json, sys
for f in sys.stdin.read().splitlines():
    if f.endswith(".json"):
        try: json.load(open(f))
        except (ValueError, OSError): print("skipping unfinished " + f, file=sys.stderr); continue
    print(f)'
}
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
  checkpoint)
    echo $$ > "$PIDF"
    while :; do
      sleep $(( ${3:-15} * 60 ))
      "$SELF" save "$2 checkpoint $(date -u +%H:%MZ)" || true
    done
    ;;
  save)
    [[ -d results ]] || { echo "nothing to save"; exit 0; }
    # the final save stops the job's checkpoint loop (a checkpoint calling save never does: it is that loop's child)
    if [[ ${2:-} != *" checkpoint "* && -f $PIDF ]]; then kill "$(cat "$PIDF")" 2>/dev/null || true; rm -f "$PIDF"; fi
    lock || exit 1
    trap unlock EXIT
    git config user.name "fma-soak bot"; git config user.email "fma-soak@users.noreply.github.com"
    # CI checkouts don't keep the token in .git/config (installers run on the same VM), so only this push sees it
    AUTH=()
    [[ -n ${RESULTS_TOKEN:-} ]] && AUTH=(-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$RESULTS_TOKEN" | base64 | tr -d '\n')")
    OUT=$(mktemp -d)
    touch "$SUMS"
    sums | grep -vxFf "$SUMS" | sed 's/^[0-9a-f]*  //' | whole_json > "$OUT/changed" || true   # new or changed since restore
    # restored files this job deleted (pool.py drops a gone host's state). Copying only changed files never removed
    # them, so dead pool hosts' leftovers sat in held.py's daily list for 10 days (2026-11-16)
    sed 's/^[0-9a-f]*  //' "$SUMS" | while IFS= read -r f; do [[ -e $f ]] || echo "$f"; done > "$OUT/deleted"
    echo "$(wc -l < "$OUT/changed" | tr -d ' ') changed files, $(wc -l < "$OUT/deleted" | tr -d ' ') deleted"
    for i in 1 2 3 4 5 6 7 8 9 10; do   # up to 10 soak jobs plus the pool save at about the same time
      rm -rf "$OUT/w"; git worktree prune
      if git fetch -q origin "$BR" 2>/dev/null; then git worktree add -q --detach "$OUT/w" FETCH_HEAD
      else git worktree add -q --detach "$OUT/w" && git -C "$OUT/w" checkout -q --orphan tmp && git -C "$OUT/w" rm -rqf . ; fi
      rsync -a --ignore-times --files-from="$OUT/changed" ./ "$OUT/w/"   # same size + mtime would be skipped
      while IFS= read -r f; do rm -f "$OUT/w/$f"; done < "$OUT/deleted"
      git -C "$OUT/w" add -A results
      git -C "$OUT/w" diff --cached --quiet && { echo "no changes"; exit 0; }
      git -C "$OUT/w" commit -qm "$2"
      git -C "$OUT/w" ${AUTH[@]+"${AUTH[@]}"} push -q origin "HEAD:refs/heads/$BR" && { echo "saved to $BR"; exit 0; }
      sleep $((RANDOM % 20 + 5))   # the other job pushed first: redo on top of it
    done
    exit 1
    ;;
  *) echo "usage: $0 restore|save <msg>"; exit 2 ;;
esac
