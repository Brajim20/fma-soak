#!/bin/zsh
# Daily L0 run for #55961: catalog audit (with network checks), then Fleet-side confirmation of suspects.
# Scheduled by ~/Library/LaunchAgents/com.fleetdm.qa.fma-soak-audit.plist (07:00 daily).
# Remove with: launchctl bootout gui/$(id -u)/com.fleetdm.qa.fma-soak-audit && rm ~/Library/LaunchAgents/com.fleetdm.qa.fma-soak-audit.plist
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd "${0:A:h}" || exit 1
mkdir -p ../logs && LOG=../logs/daily-$(date +%F).log
{
  echo "=== $(date '+%F %T') audit"
  python3 audit.py --network --hash-sample 20
  echo "=== $(date '+%F %T') confirm"
  python3 confirm.py
  echo "=== $(date '+%F %T') names (exists query vs the name Windows registers, #56971 class)"
  python3 names.py
  echo "=== $(date '+%F %T') digest"
  python3 ../soak/digest.py
  echo "=== $(date '+%F %T') done"
} >> "$LOG" 2>&1
