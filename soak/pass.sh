#!/bin/zsh
# One soak pass on one platform (task 4.1). loop.py runs this per platform, both side by side, and reads it fresh
# every pass, so changes here apply from the next pass without restarting the launchd job.
#   cleanup            uninstall apps an interrupted cycle left on the host (every pass)
#   canaries (3.5)      when the last canary run on this platform is older than loop.canary_every_hours
#   update path (3.7)   when the last one is older than loop.update_every_hours
#   coverage sample (4.2) until the next pass is due, minus a margin so the pass ends on time
#   watcher (4.3)      residents' upstream transitions, every pass (in CI, its own job every 2 h instead)
#   patch closed (S6)  Fleet's patch-when-closed policy over several passes, one app a day per platform
#   scenario (4.4)     Windows only: second explorer.exe, once a day
#   digest (4.6)        results/digest-<date>.md, rebuilt every pass
plat=$1
cd "${0:A:h}" || exit 1
cfg() { python3 -c "import json;print(json.load(open('../config.json'))['loop'].get('$1', $2))"; }
stamps=../results/loop
start=$(date +%s)
deadline=$(( start + $(cfg interval_hours 6) * 3600 - $(cfg end_margin_minutes 20) * 60 ))
# CI: each run starts the next when it ends (GitHub's cron is only a backup there), so a pass is a fixed length that
# fits under the 6 h job limit, with time left for setup, saving and the report
if [[ -n $FMA_SOAK_PASS_MINUTES ]]; then
  deadline=$(( start + FMA_SOAK_PASS_MINUTES * 60 - $(cfg end_margin_minutes 20) * 60 ))
fi
# fits <minutes>: true if that much time is left before the deadline. Long steps that don't fit wait for a pass that
# has room (a late scheduled run can start minutes before the next slot); their stamp stays old, so they stay due
fits() {
  (( deadline - $(date +%s) >= $1 * 60 )) && return 0
  echo "--- $(date '+%F %T') deferred: $2 needs ~$1 min, $(( (deadline - $(date +%s)) / 60 )) min left before the deadline"
  return 1
}
due() {  # due <name> <hours>: true if the stamp is missing or older than <hours>
  local f=$stamps/last-$1-$plat
  [[ ! -f $f ]] || (( start - $(cat $f) >= $2 * 3600 ))
}
worst=0
step() {
  echo "--- $(date '+%F %T') $*"
  python3 -u "$@"; local rc=$?
  (( rc > worst )) && worst=$rc
  return $rc
}
# an offline host would collect queued installs that run later, untracked: skip the pass (the digest still runs)
if ! step runner.py --online $plat; then step digest.py; exit $worst; fi
step runner.py --cleanup $plat   # apps an interrupted cycle left behind
step patch_closed.py tick --platform $plat   # patch when closed (S6): starts one a day, advances an active one
if due canaries $(cfg canary_every_hours 12) && fits $(cfg canary_budget_minutes 120) canaries; then step runner.py --canaries $plat; date +%s > $stamps/last-canaries-$plat; fi
if due update $(cfg update_every_hours 24) && fits $(cfg update_budget_minutes 90) "update path"; then step runner.py --update-all $plat; date +%s > $stamps/last-update-$plat; fi
# L3 transitions (4.3). In CI the watcher job owns this: a soak job saves hours after it restored, so running the
# check here too would overwrite the watcher job's newer files with this pass's stale copy
[[ -z $FMA_SOAK_WATCHER_JOB ]] && step watcher.py check --platform $plat
[[ $plat == windows ]] && step scenarios.py explorer2   # #51127 scenario, once a day (4.4)
# python, not `date -r`: BSD date reads -r as epoch seconds, GNU date (CI runners) as a file to stat
step sample.py --platform $plat --until "$(python3 -c "import datetime;print(datetime.datetime.fromtimestamp($deadline).isoformat(timespec='seconds'))")"
step digest.py   # rebuilds today's and yesterday's digest from everything so far
exit $worst
