#!/bin/zsh
# Installs the UTM autostart agent for the current user. Run it on the Mac that hosts the VM:
#   ./install.sh "<VM name>"            names: /Applications/UTM.app/Contents/MacOS/utmctl list
#   ./install.sh --uninstall
set -e
LABEL=com.fleetdm.qa.utm-autostart
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
SCRIPT=$HOME/Library/Scripts/start-utm-vm.sh
LOG=$HOME/Library/Logs/utm-autostart.log
domain=gui/$(id -u)

if [[ $1 == --uninstall ]]; then
  launchctl bootout $domain/$LABEL 2>/dev/null || true
  rm -f $PLIST $SCRIPT
  echo "removed $LABEL"; exit 0
fi
VM=${1:?usage: ./install.sh "<VM name>"   (list them with: /Applications/UTM.app/Contents/MacOS/utmctl list)}
xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<< "$1"; }

mkdir -p ${SCRIPT:h} ${PLIST:h} ${LOG:h}
cp "${0:A:h}/start-utm-vm.sh" $SCRIPT && chmod +x $SCRIPT
cat > $PLIST <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$(xml $SCRIPT)</string><string>$(xml $VM)</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>900</integer>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>StandardOutPath</key><string>$(xml $LOG)</string>
  <key>StandardErrorPath</key><string>$(xml $LOG)</string>
</dict>
</plist>
PL
plutil -lint $PLIST >/dev/null
[[ -n $DRY_RUN ]] && { cat $PLIST; exit 0; }
launchctl bootout $domain/$LABEL 2>/dev/null || true
launchctl enable $domain/$LABEL
launchctl bootstrap $domain $PLIST
echo "installed $LABEL for VM \"$VM\"; log: $LOG"
