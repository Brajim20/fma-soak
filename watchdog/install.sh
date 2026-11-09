#!/bin/zsh
# Installs the VM watchdog as a LaunchAgent for this user (runs every 10 min). Needs ~/.config/fma-vm-watchdog/token.
set -e
REPO=${0:A:h:h}
[[ -s ~/.config/fma-vm-watchdog/token ]] || { echo "first: put a qa.fleetdm.com API token in ~/.config/fma-vm-watchdog/token (chmod 600)"; exit 1; }
P=~/Library/LaunchAgents/com.fleetdm.qa.vm-watchdog.plist
sed "s|REPO|$REPO|" $REPO/watchdog/com.fleetdm.qa.vm-watchdog.plist > $P
launchctl bootout gui/$UID $P 2>/dev/null || true
launchctl bootstrap gui/$UID $P
/usr/bin/python3 $REPO/watchdog/vm-watchdog.py --dry-run
echo "installed; log: /tmp/fma-vm-watchdog.log"
