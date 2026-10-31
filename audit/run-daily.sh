#!/bin/zsh
# Daily L0 run for #55961: catalog audit (with network checks), then Fleet-side confirmation of suspects.
# Scheduled by ~/Library/LaunchAgents/com.fleetdm.qa.fma-soak-audit.plist (07:00 daily).
# Remove with: launchctl bootout gui/$(id -u)/com.fleetdm.qa.fma-soak-audit && rm ~/Library/LaunchAgents/com.fleetdm.qa.fma-soak-audit.plist
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd "${0:A:h}" || exit 1
mkdir -p ../logs && LOG=../logs/daily-$(date +%F).log
# CI runs this from more than one place (the soak run's watch job, and a backup cron): once a day is enough
[[ -n $FMA_SOAK_AUDIT_ONCE && -f ../results/audit-$(date +%F).json ]] && { echo "audit for $(date +%F) already done"; exit 0; }
{
  echo "=== $(date '+%F %T') audit"
  python3 audit.py --network --hash-sample 20
  echo "=== $(date '+%F %T') confirm"
  python3 confirm.py
  echo "=== $(date '+%F %T') names (exists query vs the name Windows registers, #56971 class)"
  python3 names.py
  # in CI the report job owns the digest; a digest saved from here could be older than the soak records
  if [[ -z $FMA_SOAK_NO_DIGEST ]]; then echo "=== $(date '+%F %T') digest"; python3 ../soak/digest.py; fi
  echo "=== $(date '+%F %T') done"
} >> "$LOG" 2>&1
