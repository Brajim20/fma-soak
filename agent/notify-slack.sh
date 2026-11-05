#!/bin/zsh
# Sends the triage inbox summary to Slack. watch.sh calls it after each run; run it by hand to test.
# The webhook URL lives in ~/.config/fma-triage/slack-webhook-url (chmod 600; the agent's sandbox can't read ~/.config).
# Works with a Workflow Builder webhook (variables: summary, drafts, needs, retested, inbox) or an incoming webhook (text).
# Icon: a Workflow Builder webhook posts with the workflow's own icon (payload icon fields are ignored); upload agent/assets/fleet-icon.png in the workflow's settings.
#   agent/notify-slack.sh            # send the current INBOX.md summary
export PATH=/opt/homebrew/bin:/usr/bin:/bin
URLF=~/.config/fma-triage/slack-webhook-url
OUT=${FMA_TRIAGE_OUT:-$HOME/Documents/GitHub/fleet-playwright/qa-stuff/fma-soak/triage}
INBOX=$OUT/INBOX.md
REPO=${0:A:h:h}
[[ -f $URLF && -f $INBOX ]] || { echo "no webhook ($URLF) or no inbox ($INBOX)"; exit 0; }
summary=$(sed -n 3p $INBOX | cut -d. -f2 | sed 's/^ //')
# draft titles only (no hosts, records or paths beyond the inbox's own)
drafts=$(awk '/^## Bug drafts/{f=1;next} /^## /{f=0} f && /^- \*\*\[/{sub(/^- \*\*\[/,"• "); sub(/\]\(.*/,""); print}' $INBOX)
needs=$(awk '/^## Needs a check/{f=1;next} /^## /{f=0} f && /^- \*\*/{sub(/^- \*\*/,"• "); sub(/\*\*:.*/,""); print}' $INBOX)
# filed bugs the soak retested after their fix (soak/recheck.py), each with its drafted QA comment copied next to the
# inbox; only verdicts worth a look: fixed, still broken, different. Nothing is posted.
retested=""
git -C $REPO fetch -q origin results 2>/dev/null
q=$(git -C $REPO show origin/results:results/state/recheck.json 2>/dev/null)
if [[ -n $q ]]; then
  mkdir -p $OUT/recheck
  while IFS=$'\t' read -r n slug st; do
    [[ -n $n ]] || continue
    case $st in
      fixed) mark="✅ retested, passed" ;;
      "still broken") mark="❌ retested, still broken" ;;
      *) mark="⚠️ retested, different findings" ;;
    esac
    line="• #$n $slug: $mark"
    git -C $REPO show "origin/results:results/recheck/${n}.md" > $OUT/recheck/$n.md 2>/dev/null \
      && line+=" (comment draft: qa-stuff/fma-soak/triage/recheck/$n.md)" || rm -f $OUT/recheck/$n.md
    retested+="$line"$'\n'
  done < <(print -r -- "$q" | jq -r '.items[]? | select(.status == "fixed" or .status == "still broken" or .status == "different")
                                     | [.issue, .slug, .status] | @tsv')
fi
retested=${retested%$'\n'}
payload=$(jq -n --arg s "$summary" --arg d "${drafts:-none}" --arg n "${needs:-none}" --arg r "${retested:-none}" \
  --arg i "qa-stuff/fma-soak/triage/INBOX.md" \
  '{summary: $s, drafts: $d, needs: $n, retested: $r, inbox: $i,
    text: ("*FMA triage*: " + $s + "\n\n*Bug drafts*\n" + $d + "\n\n*Needs a check*\n" + $n
           + "\n\n*Retested*\n" + $r + "\n\nOpen " + $i)}')
[[ -n $FMA_TRIAGE_DRY_RUN ]] && { print -r -- "$payload" | jq -r .text; exit 0; }
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$payload" "$(cat $URLF)")
echo "slack: HTTP $code"
