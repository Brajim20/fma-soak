#!/bin/zsh
# Triage agent: reads the soak's untriaged findings from the `results` branch and has a headless Claude decide each
# one (bug / already filed / environment / harness / needs you), drafting bugs in Fleet's template. Nothing is filed:
# the agent can't run gh issue create/comment/edit, git push, fleetctl or python, can only write inside its output
# directory, and gets no Fleet token or hosts (it works from the records). Brayan reads DECISIONS.md and files.
#
#   agent/triage-agent.sh [max-apps]      # default 10
#
# 1. rules in triage_queue.py settle what the records alone prove (free);
# 2. the rest are triaged in batches of FMA_TRIAGE_BATCH (5) on FMA_TRIAGE_MODEL (sonnet), FMA_TRIAGE_BATCH_BUDGET
#    (1) per app in the batch;
# 3. each `bug` is reviewed by FMA_TRIAGE_REVIEW_MODEL (opus, FMA_TRIAGE_REVIEW_BUDGET 3), which tries to prove it
#    wrong (agent/REVIEW.md) and finalizes the draft.
# Ceilings, not prices (the CLI's estimate at API prices; on a claude.ai login it counts against the plan's usage
# limits); FMA_TRIAGE_BUDGET (30) per run: no new session starts past it. FMA_TRIAGE_OUT: default <QA workspace>/
# qa-stuff/fma-soak/triage.
# Output: $FMA_TRIAGE_OUT/<date-time>/{DECISIONS.md, decisions.json, apps/<app>/{BUG_DRAFT_*.md,notes.md,decision.json},
# logs/, queue.json, rules.json, records/}; usage per session in $FMA_TRIAGE_OUT/costs.tsv.
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
  "deny": ["Agent", "Task", "Workflow", "Skill", "WebSearch",
           "Bash(gh issue create:*)", "Bash(gh issue comment:*)", "Bash(gh issue edit:*)", "Bash(gh issue close:*)",
           "Bash(gh api:*)", "Bash(gh pr create:*)", "Bash(gh pr comment:*)", "Bash(gh pr review:*)", "Bash(git push:*)",
           "Bash(fleetctl:*)", "Bash(python3:*)", "Bash(python:*)", "Bash(sh:*)", "Bash(bash:*)", "Bash(zsh:*)",
           "Bash(hdiutil:*)", "Bash(open:*)", "Bash(osascript:*)",
           "Read(//**/.env)", "Read(//**/.env.*)", "Read(//**/.auth/**)", "Read(//**/config.local.json)", "Read(~/.config/**)",
           "Read(~/.fleet/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.netrc)", "Read(~/.npmrc)", "Read(~/.docker/**)",
           "Read(~/.gitconfig)", "Read(~/.git-credentials)", "Read(~/.kube/**)", "Read(~/.gnupg/**)", "Read(~/Library/**)",
           "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(grep:*)",
           "Bash(curl * file:*)", "Bash(curl *-o /*..*)", "Bash(curl *-d *)", "Bash(curl *-F *)", "Bash(curl *-T *)",
           "Bash(curl *--data*)", "Bash(curl *--json*)", "Bash(curl *--form*)", "Bash(curl *--upload-file*)",
           "Bash(curl *-K *)", "Bash(curl *--config*)", "Bash(curl *@*)", "Bash(curl *-X *)", "Bash(curl *--request*)",
           "Bash(curl *-H *)", "Bash(curl *--header*)", "Bash(curl *;*)", "Bash(curl *|*)", "Bash(curl *&*)"]}}
EOF

# the output directory is the session's only working directory, so it's the only place the agent can write
cd $OUT
[[ -f $OUTROOT/costs.tsv ]] || printf 'run\tusd_estimate\tinput_tokens\tcache_read_tokens\tcache_write_tokens\toutput_tokens\tturns\tminutes\n' > $OUTROOT/costs.tsv
n=$(jq length queue.json) spent=0 batch=${FMA_TRIAGE_BATCH:-5}
echo "triage agent: $n apps for the agent, $(jq length rules.json) settled by rules -> $OUT"
# session <log-name> <model> <budget> <prompt>: one headless session, its usage appended to costs.tsv
session() {
  claude -p "$4" --settings $OUT/.settings.json --permission-mode dontAsk --model $2 --max-budget-usd $3 \
    --no-session-persistence --output-format json > logs/$1.json 2> logs/$1.log || echo "   $1: session exited $?"
  jq -r '.result // empty' logs/$1.json >> logs/$1.log 2>/dev/null
  # modelUsage, not usage: usage can come back zeroed (e.g. a session cut off at its ceiling)
  local row=$(jq -r --arg run "${OUT:t}/$1" '(.modelUsage // {} | [.[]]) as $m | [$run, (.total_cost_usd // 0),
    ($m | map(.inputTokens // 0) | add // 0), ($m | map(.cacheReadInputTokens // 0) | add // 0),
    ($m | map(.cacheCreationInputTokens // 0) | add // 0), ($m | map(.outputTokens // 0) | add // 0),
    (.num_turns // 0), ((.duration_ms // 0) / 60000 | floor)] | map(tostring) | @tsv' logs/$1.json 2>/dev/null)
  [[ -n $row ]] && echo "$row" >> $OUTROOT/costs.tsv
  spent=$(echo "$spent + $(echo "$row" | cut -f2 | grep -E '^[0-9.]+$' || echo 0)" | bc)
}
over() { (( $(echo "$spent >= ${FMA_TRIAGE_BUDGET:-30}" | bc) )) && echo "run ceiling reached (\$$spent): the rest stay queued"; }
mkdir -p logs
# 1. triage in batches on the cheaper model: one startup cost per batch, a context that stays short
for (( s0 = 0; s0 < n; s0 += batch )); do
  over && break
  e=$(( s0 + batch < n ? s0 + batch : n )) list=""
  for (( i = s0; i < e; i++ )); do
    slug=$(jq -r ".[$i].slug" queue.json); app=${${slug//\//-}//@/-}; mkdir -p apps/$app
    list+=" item $i ($slug) -> $OUT/apps/$app/;"
  done
  session triage-$s0 ${FMA_TRIAGE_MODEL:-sonnet} $(( ${FMA_TRIAGE_BATCH_BUDGET:-1} * (e - s0) )) \
    "Read $HERE/PROMPT.md and follow it for these queue items in $OUT/queue.json, each into its own folder:$list Output directory: $OUT."
  for (( i = s0; i < e; i++ )); do
    slug=$(jq -r ".[$i].slug" queue.json); app=${${slug//\//-}//@/-}
    echo "   $slug: $(jq -r '.decision // "undecided"' apps/$app/decision.json 2>/dev/null || echo undecided)"
  done
done
# 2. every bug call gets a stricter model that tries to prove it wrong before it reaches Brayan
for d in apps/*/decision.json(N); do
  [[ $(jq -r .decision $d 2>/dev/null) == bug ]] || continue
  over && break
  app=${d:h:t}
  session review-$app ${FMA_TRIAGE_REVIEW_MODEL:-opus} ${FMA_TRIAGE_REVIEW_BUDGET:-3} \
    "Read $HERE/REVIEW.md and follow it for the app in $OUT/apps/$app/. Output directory: $OUT."
  echo "   review $app: $(jq -r '.reviewed // .decision' $d 2>/dev/null | cut -c1-100)"
done
echo "spent ~\$$spent (estimate)"
python3 $W/soak/triage_queue.py --assemble $OUT
python3 $W/soak/triage_queue.py --record-state $OUT --state $OUTROOT/state.json
python3 $W/soak/triage_queue.py --inbox $OUTROOT
rm -f $OUT/.settings.json
echo "read: $OUTROOT/INBOX.md (this run: $OUT/DECISIONS.md)"
