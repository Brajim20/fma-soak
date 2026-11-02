#!/bin/zsh
# Sends the triage inbox summary to Slack. watch.sh calls it after each run; run it by hand to test.
# The webhook URL lives in ~/.config/fma-triage/slack-webhook-url (chmod 600; the agent's sandbox can't read ~/.config).
# Works with a Workflow Builder webhook (variables: summary, drafts, needs, inbox) or an incoming webhook (text).
#   agent/notify-slack.sh            # send the current INBOX.md summary
export PATH=/opt/homebrew/bin:/usr/bin:/bin
URLF=~/.config/fma-triage/slack-webhook-url
INBOX=${FMA_TRIAGE_OUT:-$HOME/Documents/GitHub/fleet-playwright/qa-stuff/fma-soak/triage}/INBOX.md
[[ -f $URLF && -f $INBOX ]] || { echo "no webhook ($URLF) or no inbox ($INBOX)"; exit 0; }
summary=$(sed -n 3p $INBOX | cut -d. -f2 | sed 's/^ //')
# draft titles only (no hosts, records or paths beyond the inbox's own)
drafts=$(awk '/^## Bug drafts/{f=1;next} /^## /{f=0} f && /^- \*\*\[/{sub(/^- \*\*\[/,"• "); sub(/\]\(.*/,""); print}' $INBOX)
needs=$(awk '/^## Needs a check/{f=1;next} /^## /{f=0} f && /^- \*\*/{sub(/^- \*\*/,"• "); sub(/\*\*:.*/,""); print}' $INBOX)
payload=$(jq -n --arg s "$summary" --arg d "${drafts:-none}" --arg n "${needs:-none}" --arg i "qa-stuff/fma-soak/triage/INBOX.md" \
  '{summary: $s, drafts: $d, needs: $n, inbox: $i,
    text: ("*FMA triage*: " + $s + "\n\n*Bug drafts*\n" + $d + "\n\n*Needs a check*\n" + $n + "\n\nOpen " + $i)}')
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$payload" "$(cat $URLF)")
echo "slack: HTTP $code"
