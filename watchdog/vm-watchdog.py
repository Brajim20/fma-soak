#!/usr/bin/env python3
"""Restart a soak VM that froze (2026-11-08: 631 and 624 hung on 4 GB VMs during multi-GB installs and stayed
offline for hours, because an offline host can't receive Fleet's MDM restart).

Runs on the Mac that hosts the UTM VMs, every 10 min (launchd, com.fleetdm.qa.vm-watchdog.plist). For each UTM VM
whose MAC address matches a soak host's primary MAC in Fleet, it restarts the VM (utm://stop then utm://start, which
needs no Automation permission, unlike utmctl) when Fleet has seen that host offline for OFFLINE_MIN minutes, at
most once per COOLDOWN_MIN. VMs that match no soak host are never touched. If Fleet can't be read, nothing happens:
a network outage must not restart every VM. The VMs sign in on their own, so the soak resumes without a person.

Setup (once per Mac): put a Fleet API token for qa.fleetdm.com in ~/.config/fma-vm-watchdog/token (chmod 600).
  python3 vm-watchdog.py --dry-run   # prints the VM <-> host matches and what it would do
"""
import datetime, glob, json, os, plistlib, subprocess, sys, time, urllib.parse, urllib.request

BASE = os.environ.get("FLEET_URL", "https://qa.fleetdm.com") + "/api/latest/fleet"
HOSTS = [int(h) for h in os.environ.get("SOAK_HOSTS", "624,631,636").split(",")]
OFFLINE_MIN = int(os.environ.get("OFFLINE_MIN", "60"))
COOLDOWN_MIN = int(os.environ.get("COOLDOWN_MIN", "120"))
DOCS = os.path.expanduser("~/Library/Containers/com.utmapp.UTM/Data/Documents")
CFG = os.path.expanduser("~/.config/fma-vm-watchdog")
STATE = os.path.join(CFG, "state.json")
DRY = "--dry-run" in sys.argv


def log(msg):
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def mac(s):
    return ":".join(f"{int(x, 16):02x}" for x in s.split(":")) if s else None


def vm_macs():
    """{mac: VM name} for every UTM VM on this Mac."""
    out = {}
    def walk(o, found):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "MacAddress" and isinstance(v, str):
                    found.append(v)
                walk(v, found)
        elif isinstance(o, list):
            for v in o:
                walk(v, found)
    for b in glob.glob(os.path.join(DOCS, "*.utm")):
        try:
            c = plistlib.load(open(os.path.join(b, "config.plist"), "rb"))
        except Exception:
            continue
        name = (c.get("Information") or {}).get("Name") or os.path.basename(b)[:-4]
        found = []
        walk(c, found)
        for m in found:
            out[mac(m)] = name
    return out


def host(hid, token):
    req = urllib.request.Request(f"{BASE}/hosts/{hid}", headers={"Authorization": "Bearer " + token, "User-Agent": "fleet-qa-vm-watchdog"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["host"]


def utm(action, name):
    subprocess.run(["open", "-g", f"utm://{action}?name={urllib.parse.quote(name)}"], check=False)


def main():
    token = open(os.path.join(CFG, "token")).read().strip()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    vms = vm_macs()
    now = datetime.datetime.now(datetime.timezone.utc)
    for hid in HOSTS:
        try:
            h = host(hid, token)
        except Exception as e:
            log(f"host {hid}: can't read Fleet ({e}); doing nothing")
            return
        name = vms.get(mac(h.get("primary_mac")))
        if not name:
            continue  # not on this Mac
        seen = datetime.datetime.fromisoformat(h["seen_time"].replace("Z", "+00:00"))
        off = (now - seen).total_seconds() / 60
        last = state.get(str(hid))
        since_restart = (now - datetime.datetime.fromisoformat(last)).total_seconds() / 60 if last else None
        if h.get("status") != "offline" or off < OFFLINE_MIN:
            if DRY:
                log(f"host {hid} = VM '{name}': {h.get('status')}, seen {off:.0f} min ago; nothing to do")
            continue
        if since_restart is not None and since_restart < COOLDOWN_MIN:
            log(f"host {hid} = VM '{name}': offline {off:.0f} min, but restarted {since_restart:.0f} min ago; waiting")
            continue
        log(f"host {hid} = VM '{name}': offline {off:.0f} min; {'would restart' if DRY else 'restarting'}")
        if DRY:
            continue
        utm("stop", name)
        time.sleep(45)
        utm("start", name)
        state[str(hid)] = now.isoformat()
        os.makedirs(CFG, exist_ok=True)
        json.dump(state, open(STATE, "w"))


if __name__ == "__main__":
    main()
