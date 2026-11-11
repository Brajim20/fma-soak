#!/usr/bin/env python3
"""Restart a soak VM that froze (2026-11-08: 631 and 624 hung on 4 GB VMs during multi-GB installs and stayed
offline for hours, because an offline host can't receive Fleet's MDM restart).

Runs on the Mac that hosts the UTM VMs, every 10 min (launchd, com.fleetdm.qa.vm-watchdog.plist). For each UTM VM
whose MAC address matches a soak host's primary MAC in Fleet, it restarts the VM (utm://stop then utm://start, which
needs no Automation permission, unlike utmctl) when Fleet has seen that host offline for OFFLINE_MIN minutes, at
most once per COOLDOWN_MIN. VMs that match no soak host are never touched. If Fleet can't be read, nothing happens:
a network outage must not restart every VM. The VMs sign in on their own, so the soak resumes without a person.
Parallels VMs (629, Windows ARM) restart with prlctl stop --kill / start; name them "parallels:<name>" in vms.json.

It also posts to Slack when soak hosts have been offline for ALERT_MIN minutes, in one message per batch, and again
when they're back. That's for what a restart can't fix: on 2026-11-10/11 629, 631 and 636 were offline together for
16 h (their Mac or its network, not a VM), and the only alert was a GitHub issue nobody saw overnight. Every soak host
is checked, not just this Mac's, so the watchdog on the other Mac reports this one's VMs when this Mac goes down.
Needs a webhook: ~/.config/fma-vm-watchdog/slack-webhook-url, else the triage one (~/.config/fma-triage/...).

Setup (once per Mac): put a Fleet API token for qa.fleetdm.com in ~/.config/fma-vm-watchdog/token (chmod 600), and
the VMs on this Mac in ~/.config/fma-vm-watchdog/vms.json, e.g. {"624": "macOS new vm for upadte"} (UTM's names), or
{"629": "parallels:Windows 11"} for a Parallels VM (prlctl list -a).
  python3 vm-watchdog.py --dry-run   # prints the VM <-> host matches and what it would do
"""
import datetime, glob, json, os, plistlib, subprocess, sys, time, urllib.parse, urllib.request

BASE = os.environ.get("FLEET_URL", "https://qa.fleetdm.com") + "/api/latest/fleet"
HOSTS = [int(h) for h in os.environ.get("SOAK_HOSTS", "624,629,631,635,636").split(",")]
OFFLINE_MIN = int(os.environ.get("OFFLINE_MIN", "60"))
COOLDOWN_MIN = int(os.environ.get("COOLDOWN_MIN", "120"))
ALERT_MIN = int(os.environ.get("ALERT_MIN", "120"))
PRLCTL = "/usr/local/bin/prlctl"
DOCS = os.path.expanduser("~/Library/Containers/com.utmapp.UTM/Data/Documents")
CFG = os.path.expanduser("~/.config/fma-vm-watchdog")
STATE = os.path.join(CFG, "state.json")
VMS = os.path.join(CFG, "vms.json")
WEBHOOKS = [os.path.join(CFG, "slack-webhook-url"), os.path.expanduser("~/.config/fma-triage/slack-webhook-url")]
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
        except Exception as e:
            log(f"can't read {b}: {e}")
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


def restart(name):
    if name.startswith("parallels:"):
        vm = name.split(":", 1)[1]
        subprocess.run([PRLCTL, "stop", vm, "--kill"], check=False)
        time.sleep(15)
        subprocess.run([PRLCTL, "start", vm], check=False)
        return
    utm("stop", name)
    time.sleep(45)
    utm("start", name)


def slack(text):
    """Post text; returns True when sent. A Workflow Builder webhook (the triage one) takes `summary`, the others
    say none; an incoming webhook takes `text`."""
    url = next((open(f).read().strip() for f in WEBHOOKS if os.path.exists(f)), None)
    if not url:
        return False
    body = {"text": text, "summary": text, "drafts": "none", "needs": "none", "retested": "none",
            "inbox": "host alert from vm-watchdog"}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception as e:
        log(f"slack: {e}")
        return False


def outages(hosts, state, now):
    """The Slack messages to send for hosts that crossed ALERT_MIN offline or came back, and the updated
    state["alerted"] ({host: since}). One message per direction, listing every host in it."""
    alerted = dict(state.get("alerted", {}))
    down, up = [], []
    for hid, h in hosts.items():
        off = (now - seen(h)).total_seconds() / 60
        if h.get("status") == "offline" and off >= ALERT_MIN:
            if str(hid) not in alerted:
                alerted[str(hid)] = h["seen_time"]
                down.append(f"{hid} ({h.get('hostname') or '?'}, last seen {h['seen_time'][:16]}Z)")
        elif str(hid) in alerted and h.get("status") != "offline":
            alerted.pop(str(hid))
            up.append(str(hid))
    msgs = []
    if down:
        msgs.append(f"⚠️ FMA soak hosts offline {ALERT_MIN // 60}h+: {', '.join(down)}. "
                    "Several at once usually means their Mac is asleep, off or off the network.")
    if up:
        msgs.append(f"✅ FMA soak hosts back online: {', '.join(up)}")
    return msgs, alerted


def seen(h):
    return datetime.datetime.fromisoformat(h["seen_time"].replace("Z", "+00:00"))


def main():
    token = open(os.path.join(CFG, "token")).read().strip()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    # macOS keeps other apps (Terminal, launchd jobs) out of UTM's container, so the usual source is vms.json:
    # {"<fleet host id>": "<UTM VM name>"}, only the VMs on this Mac. MAC matching is used when the container is readable.
    named = json.load(open(VMS)) if os.path.exists(VMS) else {}
    vms = vm_macs() if not named else {}
    if not named and not vms:
        log(f"no UTM VM readable in {DOCS} and no {VMS}; alerts only, no restarts")
    now = datetime.datetime.now(datetime.timezone.utc)
    hosts = {}
    for hid in HOSTS:
        try:
            hosts[hid] = host(hid, token)
        except Exception as e:
            log(f"host {hid}: can't read Fleet ({e}); doing nothing")
            return
    msgs, alerted = outages(hosts, state, now)
    for m in msgs:
        log(f"slack: {m}")
        if not DRY and not slack(m):
            return  # not sent: keep the old state so the next run tries again
    if not DRY and alerted != state.get("alerted", {}):
        state["alerted"] = alerted
        os.makedirs(CFG, exist_ok=True)
        json.dump(state, open(STATE, "w"))
    for hid, h in hosts.items():
        name = named.get(str(hid)) or vms.get(mac(h.get("primary_mac")))
        if not name:
            continue  # not on this Mac
        off = (now - seen(h)).total_seconds() / 60
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
        restart(name)
        state[str(hid)] = now.isoformat()
        os.makedirs(CFG, exist_ok=True)
        json.dump(state, open(STATE, "w"))


if __name__ == "__main__":
    main()
