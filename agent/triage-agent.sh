#!/bin/zsh
# Triage agent: reads the soak's untriaged findings from the `results` branch and has a headless Claude decide each
# one (bug / already filed / environment / harness / needs you), drafting bugs in Fleet's template. Nothing is filed:
# the agent can't run gh issue create/comment/edit, git push, fleetctl or python, can only write inside its output
# directory, and gets no Fleet token or hosts (it works from the records). Brayan reads DECISIONS.md and files.
#
#   agent/triage-agent.sh [max-apps]      # default 10
#
# Rules in triage_queue.py settle what the records alone prove (free); the rest get one short agent session each, so
# an app's cost doesn't grow with the queue. Ceilings, not prices (the CLI's estimate at API prices; on a claude.ai
# login it counts against the plan's usage limits): FMA_TRIAGE_APP_BUDGET per app (default 2), FMA_TRIAGE_BUDGET per
# run (default 30; no new app starts past it). Other env: FMA_TRIAGE_OUT (default <QA workspace>/qa-stuff/fma-soak/
# triage), FMA_TRIAGE_MODEL (default: the CLI's default).
# Output: $FMA_TRIAGE_OUT/<date-time>/{DECISIONS.md, decisions.json, apps/<app>/{BUG_DRAFT_*.md,notes.md,decision.json,
# agent.log}, queue.json, rules.json, records/}; usage per app in $FMA_TRIAGE_OUT/costs.tsv.
# Apps decided aren't queued again until a newer finding or the other host's rerun ($FMA_TRIAGE_OUT/state.json).
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
if [[ $(jq length $OUT/queue.json) == 0 && $(jq length $OUT/rules.json) == 0 ]]; then
  echo "nothing to triage"; rm -r -- "$OUT"; exit 0
fi
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
[[ -f $OUTROOT/costs.tsv ]] || printf 'run\tusd_estimate\tinput_tokens\tcache_read_tokens\tcache_write_tokens\toutput_tokens\tturns\tminutes\n' > $OUTROOT/costs.tsv
n=$(jq length queue.json) spent=0
echo "triage agent: $n apps for the agent, $(jq length rules.json) settled by rules -> $OUT"
for i in $(seq 0 $(( n - 1 ))); do
  if (( $(echo "$spent >= ${FMA_TRIAGE_BUDGET:-30}" | bc) )); then echo "run ceiling reached (\$$spent): the rest stay queued"; break; fi
  slug=$(jq -r ".[$i].slug" queue.json); app=${${slug//\//-}//@/-}
  mkdir -p apps/$app
  # one session per app: its context holds this app only
  claude -p "Read $HERE/PROMPT.md and follow it for ONE app: queue item $i ($slug) in $OUT/queue.json. Output directory: $OUT. Write this app's files into $OUT/apps/$app/." \
    --settings $OUT/.settings.json --permission-mode dontAsk \
    --max-budget-usd ${FMA_TRIAGE_APP_BUDGET:-2} ${FMA_TRIAGE_MODEL:+--model $FMA_TRIAGE_MODEL} \
    --no-session-persistence --output-format json > apps/$app/agent.json 2> apps/$app/agent.log || echo "   $slug: agent exited $?"
  jq -r '.result // empty' apps/$app/agent.json >> apps/$app/agent.log 2>/dev/null
  row=$(jq -r --arg run "${OUT:t}/$app" '[$run, (.total_cost_usd // 0), (.usage.input_tokens // 0), (.usage.cache_read_input_tokens // 0),
    (.usage.cache_creation_input_tokens // 0), (.usage.output_tokens // 0), (.num_turns // 0), ((.duration_ms // 0) / 60000 | floor)]
    | map(tostring) | @tsv' apps/$app/agent.json 2>/dev/null) && echo "$row" >> $OUTROOT/costs.tsv
  spent=$(echo "$spent + $(echo "$row" | cut -f2 | grep -E '^[0-9.]+$' || echo 0)" | bc)
  echo "   $slug: $(jq -r '.decision // "undecided"' apps/$app/decision.json 2>/dev/null || echo undecided) (\$$(echo "$row" | cut -f2 | cut -c1-5))"
done
echo "spent ~\$$spent (estimate)"
python3 $W/soak/triage_queue.py --assemble $OUT
python3 $W/soak/triage_queue.py --record-state $OUT --state $OUTROOT/state.json
rm -f $OUT/.settings.json
echo "read: $OUT/DECISIONS.md"
