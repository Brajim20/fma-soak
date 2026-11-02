#!/bin/zsh
# Triage agent: reads the soak's untriaged findings from the `results` branch and has a headless Claude decide each
# one (bug / already filed / environment / harness / needs you), drafting bugs in Fleet's template. Nothing is filed:
# the agent can't run gh issue create/comment/edit, git push, fleetctl or python, can only write inside its output
# directory, and gets no Fleet token or hosts (it works from the records). Brayan reads DECISIONS.md and files.
#
#   agent/triage-agent.sh [max-apps]      # default 10
#
# Env: FMA_TRIAGE_OUT (default <QA workspace>/qa-stuff/fma-soak/triage), FMA_TRIAGE_BUDGET (USD ceiling, default 15;
#      a cap, not a price: the run stops there),
#      FMA_TRIAGE_MODEL (default: the CLI's default).
# Output: $FMA_TRIAGE_OUT/<date-time>/{queue.json,records/,DECISIONS.md,decisions.json,BUG_DRAFT_*.md,agent.log}.
# Apps the agent decided aren't queued again until they have a newer finding ($FMA_TRIAGE_OUT/state.json).
set -euo pipefail
HERE=${0:A:h}; REPO=${HERE:h}
QA=${FMA_TRIAGE_QA:-$HOME/Documents/GitHub/fleet-playwright}
CATALOG=$HOME/Documents/GitHub/fleet
OUTROOT=${FMA_TRIAGE_OUT:-$QA/qa-stuff/fma-soak/triage}
# one run at a time, whoever starts it (watch.sh or by hand); a lock older than 4 h is a crashed run
LOCK=$OUTROOT/.agent-running
mkdir -p $OUTROOT
if ! mkdir $LOCK 2>/dev/null; then
  if [[ -n $(find $LOCK -maxdepth 0 -mmin +240) ]]; then rmdir $LOCK; mkdir $LOCK
  else echo "another triage run is in progress ($LOCK)"; exit 75; fi
fi
OUT=$OUTROOT/$(date +%F-%H%M)
mkdir -p $OUT

# the harness at origin/main with the CI results, in a throwaway worktree (the local checkout's results/ is stale)
git -C $REPO fetch -q origin main results
W=$(mktemp -d)/h
git -C $REPO worktree add -q --detach $W origin/main
trap 'git -C $REPO worktree remove --force $W; rmdir $LOCK' EXIT
(cd $W && git archive origin/results results | tar -x)
cp $REPO/config.local.json $W/
python3 $W/soak/triage_queue.py --out $OUT --state $OUTROOT/state.json --max ${1:-10}
if [[ $(jq length $OUT/queue.json) == 0 ]]; then echo "nothing to triage"; rm -r -- "$OUT"; exit 0; fi
git -C $CATALOG fetch -q origin main

cat > $OUT/.settings.json <<EOF
{"permissions": {
  "allow": ["Read", "Glob", "Grep", "Edit(/$OUT/**)",
            "Bash(gh issue list:*)", "Bash(gh issue view:*)", "Bash(gh search issues:*)", "Bash(gh pr list:*)", "Bash(gh pr view:*)",
            "Bash(git -C $CATALOG show:*)", "Bash(git -C $CATALOG log:*)", "Bash(git -C $CATALOG grep:*)",
            "Bash(curl -fsSL -o $OUT/downloads/*)", "Bash(curl -fsSL -o downloads/*)", "Bash(curl -fsSL -o ./downloads/*)",
            "Bash(curl -fsSIL https:*)", "Bash(curl -sIL https:*)", "Bash(curl -sSIL https:*)", "Bash(curl -IL https:*)", "Bash(curl -I https:*)", "Bash(shasum -a 256 $OUT/downloads/*)",
            "Bash(msiinfo * $OUT/downloads/*)", "Bash(pkgutil --expand $OUT/downloads/*)", "Bash(xar -tf $OUT/downloads/*)",
            "Bash(plutil -p $OUT/downloads/*)", "Bash(unzip -l $OUT/downloads/*)", "Bash(ls:*)", "Bash(mkdir -p $OUT/downloads/*)", "Bash(mkdir -p downloads/*)"],
  "deny": ["Bash(gh issue create:*)", "Bash(gh issue comment:*)", "Bash(gh issue edit:*)", "Bash(gh issue close:*)",
           "Bash(gh api:*)", "Bash(gh pr create:*)", "Bash(gh pr comment:*)", "Bash(gh pr review:*)", "Bash(git push:*)",
           "Bash(fleetctl:*)", "Bash(python3:*)", "Bash(python:*)", "Bash(sh:*)", "Bash(bash:*)", "Bash(zsh:*)",
           "Bash(hdiutil:*)", "Bash(open:*)", "Bash(osascript:*)",
           "Read(//**/.env)", "Read(//**/.env.*)", "Read(//**/.auth/**)", "Read(//**/config.local.json)", "Read(~/.config/**)",
           "Read(~/.fleet/**)", "Read(~/.ssh/**)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(grep:*)",
           "Bash(curl * file:*)", "Bash(curl *-o /*..*)", "Bash(curl *-d *)", "Bash(curl *-F *)", "Bash(curl *-T *)"]}}
EOF

# the output directory is the session's only working directory, so it's the only place the agent can write
cd $OUT
echo "triage agent: $(jq length $OUT/queue.json) apps -> $OUT"
claude -p "Read $HERE/PROMPT.md and follow it. Output directory: $OUT. The queue is $OUT/queue.json." \
  --settings $OUT/.settings.json --permission-mode dontAsk \
  --max-budget-usd ${FMA_TRIAGE_BUDGET:-15} ${FMA_TRIAGE_MODEL:+--model $FMA_TRIAGE_MODEL} \
  --no-session-persistence --output-format json > $OUT/agent.json 2> $OUT/agent.log || echo "agent exited $? (see agent.log)"
# the agent's final message, and what the run used (the CLI's estimate at API prices; on a claude.ai login it counts
# against the plan's usage limits, not a bill) -> costs.tsv, one line per run
jq -r '.result // empty' $OUT/agent.json >> $OUT/agent.log 2>/dev/null
jq -r --arg run ${OUT:t} '[$run, (.total_cost_usd // 0 | tostring), (.num_turns // 0 | tostring), ((.duration_ms // 0) / 60000 | floor | tostring) + " min"] | @tsv' \
  $OUT/agent.json >> $OUTROOT/costs.tsv 2>/dev/null && echo "used: $(tail -1 $OUTROOT/costs.tsv)"
python3 $W/soak/triage_queue.py --record-state $OUT --state $OUTROOT/state.json
rm -f $OUT/.settings.json
echo "read: $OUT/DECISIONS.md"
