# UTM autostart (soak host 631)

UTM has no "start this VM when the Mac starts" setting. This launchd agent does it: at login, and then every 15 minutes,
it starts the VM if it isn't running. While the VM is up it does nothing and logs nothing.

Parallels (629) doesn't need this: its VM is set to **Always ready in background**.

## Install (on the Mac that runs the VM, logged in as the user who owns it)

```
/Applications/UTM.app/Contents/MacOS/utmctl list     # the VM's name is the last column
./install.sh "<VM name>"
```

The first run asks for permission to control UTM ("zsh wants access to control UTM"): click **Allow**. If no prompt shows
and `~/Library/Logs/utm-autostart.log` says `-1743`, open System Settings → Privacy & Security → Automation and turn on
UTM under zsh, then `launchctl kickstart gui/$(id -u)/com.fleetdm.qa.utm-autostart`.

Check it: stop the VM in UTM, then run that `kickstart` command; the VM should start and the log should say so.

## Pause it

It restarts a stopped VM within 15 minutes, so before snapshots or maintenance:

```
touch ~/.utm-autostart-paused     # rm it when done
```

## For it to work after an unattended restart

The agent runs at login, so the Mac must reach the desktop on its own: automatic login on (not possible with FileVault
on), and "Start up automatically after a power failure". Inside the VM, sign-in should be automatic too, since some
installers (per-user apps) need a logged-in user.

## Remove

```
./install.sh --uninstall
```
