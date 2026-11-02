#!/bin/zsh
# Runs the triage agent when the soak has saved new results (launchd, every 30 min: agent/com.fleetdm.qa.fma-triage.plist).
# Cheap when nothing changed: one git fetch. When the `results` branch moved, triage-agent.sh runs on up to
# FMA_TRIAGE_MAX apps (it skips apps already decided, so a run with nothing new ends in seconds), then a macOS
# notification says what's ready. One run at a time; a run that fails leaves the branch unmarked so the next tick retries.
#   pause: touch ~/.fma-triage-paused     resume: rm ~/.fma-triage-paused
export PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
HERE=${0:A:h}; REPO=${HERE:h}
OUTROOT=${FMA_TRIAGE_OUT:-$HOME/Documents/GitHub/fleet-playwright/qa-stuff/fma-soak/triage}
SEEN=$OUTROOT/last-results-sha LOCK=$OUTROOT/.running
[[ -f ~/.fma-triage-paused ]] && exit 0
mkdir -p $OUTROOT
mkdir $LOCK 2>/dev/null || { [[ -n $(find $LOCK -maxdepth 0 -mmin +240) ]] && rmdir $LOCK && mkdir $LOCK; } || exit 0
trap 'rmdir $LOCK' EXIT
git -C $REPO fetch -q origin results || exit 0
sha=$(git -C $REPO rev-parse origin/results)
[[ -f $SEEN && $(cat $SEEN) == $sha ]] && exit 0
echo "--- $(date '+%F %T') results moved to ${sha:0:9}"
before=$(ls -d $OUTROOT/*/ 2>/dev/null | wc -l)
if $HERE/triage-agent.sh ${FMA_TRIAGE_MAX:-10} < /dev/null; then
  echo $sha > $SEEN
  after=$(ls -td $OUTROOT/*/ 2>/dev/null | head -1)
  if (( $(ls -d $OUTROOT/*/ 2>/dev/null | wc -l) > before )) && [[ -f $after/DECISIONS.md ]]; then
    msg=$(head -1 $after/DECISIONS.md | tr -d '*#' | cut -c1-120)
    [[ $msg == *triage* || -z $msg ]] && msg=$(sed -n 3p $after/DECISIONS.md | tr -d '*' | cut -c1-120)
    osascript -e "display notification \"${msg//\"/}\" with title \"FMA triage ready\" subtitle \"${after:t}\"" 2>/dev/null
  fi
fi
