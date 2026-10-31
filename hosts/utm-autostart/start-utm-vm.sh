#!/bin/zsh
# Keeps a UTM VM running: at login, then every 15 min, starts it if it's stopped (soak host 631, fleetdm/fleet#55961).
# Quiet while the VM is up; logs only when it acts. To stop it restarting the VM (snapshots, maintenance):
#   touch ~/.utm-autostart-paused        ...and rm it when done.
VM=${1:?usage: start-utm-vm.sh "<VM name or UUID, from: utmctl list>"}
UTMCTL=/Applications/UTM.app/Contents/MacOS/utmctl
PAUSE=$HOME/.utm-autostart-paused
log() { print -r -- "$(date '+%F %T') $*"; }

[[ -e $PAUSE ]] && { log "paused ($PAUSE exists): not touching $VM"; exit 0; }
[[ -x $UTMCTL ]] || { log "utmctl not found at $UTMCTL (is UTM in /Applications?)"; exit 1; }
# utmctl talks to the running app; at login UTM may not be open yet
if ! pgrep -xq UTM; then log "opening UTM"; open -ga UTM; sleep 20; fi

for i in {1..10}; do
  st=$("$UTMCTL" status "$VM" 2>&1)
  case $st in
    started|starting|resuming)
      (( i > 1 )) && log "$VM is $st"
      exit 0 ;;
    stopped|paused|suspended)
      log "$VM is $st: starting it"
      "$UTMCTL" start "$VM" 2>&1 | while IFS= read -r l; do log "utmctl: $l"; done ;;
    *-1743*)
      # macOS Automation permission: whatever runs utmctl must be allowed to control UTM
      log "not allowed to control UTM (-1743): System Settings > Privacy & Security > Automation > allow UTM for zsh (see README)"
      exit 1 ;;
    *"not found"*)
      names=$("$UTMCTL" list 2>/dev/null | grep -E '^[0-9A-Fa-f-]{36} ' | awk '{$1=$2=""; print substr($0,3)}' | paste -sd, -)
      log "no VM named \"$VM\" in UTM; names: ${names:-unknown (run: $UTMCTL list)}"
      exit 1 ;;
    *)
      log "utmctl status said: $st (try $i of 10)" ;;
  esac
  sleep 30
done
log "gave up: $VM still isn't running after 10 tries"
exit 1
