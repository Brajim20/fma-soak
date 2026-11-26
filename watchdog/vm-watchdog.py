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
is checked except this Mac's own VMs: a frozen VM gets restarted here, and when this whole Mac is down the
watchdog on the other Mac reports it, so each outage is posted once. A host on neither Mac (635) would be posted by
both; set "_alerts": [<host ids>] in one Mac's vms.json to limit what it reports (e.g. [624] on the other Mac).
Stuck inventory: a host can stay online and answer scripts while sending no software inventory (858, 2026-11-19,
12 h). When an online host's software_updated_at is STALE_MIN old and another host's is fresh, the watchdog restarts
fleetd through a Fleet script, then the VM FLEETD_WAIT_MIN later if it's still stuck, then posts to Slack. It acts on
this Mac's VMs plus hosts listed in vms.json "_stale": [<ids>] (hosts on no Mac, like the GitHub runner hosts). The
token needs script-run rights on the soak fleet for the fleetd step.
Needs a webhook: ~/.config/fma-vm-watchdog/slack-webhook-url, else the triage one (~/.config/fma-triage/...).

Setup (once per Mac): put a Fleet API token for qa.fleetdm.com in ~/.config/fma-vm-watchdog/token (chmod 600), and
the VMs on this Mac in ~/.config/fma-vm-watchdog/vms.json, e.g. {"624": "macOS new vm for upadte"} (UTM's names), or
{"629": "parallels:Windows 11"} for a Parallels VM (prlctl list -a).
  python3 vm-watchdog.py --dry-run   # prints the VM <-> host matches and what it would do
"""
import datetime, glob, json, os, plistlib, subprocess, sys, time, urllib.parse, urllib.request

BASE = os.environ.get("FLEET_URL", "https://qa.fleetdm.com") + "/api/latest/fleet"
HOSTS = [int(h) for h in os.environ.get("SOAK_HOSTS", "624,629,631,635,636,858,860,865").split(",")]
OFFLINE_MIN = int(os.environ.get("OFFLINE_MIN", "60"))
COOLDOWN_MIN = int(os.environ.get("COOLDOWN_MIN", "120"))
ALERT_MIN = int(os.environ.get("ALERT_MIN", "120"))
STALE_MIN = int(os.environ.get("STALE_MIN", "120"))        # online, but software inventory this old: stuck
FLEETD_WAIT_MIN = int(os.environ.get("FLEETD_WAIT_MIN", "60"))  # after restarting fleetd, how long before the VM
# fleetd restart scripts. The restart has to outlive the script: fleetd kills a script's background children when
# the script ends (a nohup'd "sleep; kickstart" never ran on 858, 2026-11-20), so hand it to launchd / Task Scheduler.
RESTART_FLEETD = {
    "darwin": "/bin/launchctl remove com.fleetqa.restart-orbit 2>/dev/null\n"
              "/bin/launchctl submit -l com.fleetqa.restart-orbit -- /bin/sh -c "
              "\"sleep 15; /bin/launchctl kickstart -k system/com.fleetdm.orbit; /bin/launchctl remove com.fleetqa.restart-orbit\"\n"
              "echo scheduled",
    "windows": "schtasks /Create /F /RU SYSTEM /SC ONCE /ST 00:00 /TN FleetQARestartOrbit /TR "
               "\"powershell -NoProfile -Command Start-Sleep 15; Restart-Service -Name 'Fleet osquery' -Force\" | Out-Null\n"
               "schtasks /Run /TN FleetQARestartOrbit | Out-Null\n'scheduled'",
}
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


def ts(v):
    return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def activities(hid, token):
    req = urllib.request.Request(f"{BASE}/hosts/{hid}/activities?per_page=30",
                                 headers={"Authorization": "Bearer " + token, "User-Agent": "fleet-qa-vm-watchdog"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("activities") or []


SOFTWARE_DONE = {("installed_software", "installed"), ("uninstalled_software", "uninstalled")}


def still_listed(hid, title, token):
    """True when the host's inventory still lists `title` as installed."""
    q = urllib.parse.urlencode({"query": title, "per_page": 50})
    req = urllib.request.Request(f"{BASE}/hosts/{hid}/software?{q}",
                                 headers={"Authorization": "Bearer " + token, "User-Agent": "fleet-qa-vm-watchdog"})
    with urllib.request.urlopen(req, timeout=30) as r:
        sw = json.load(r).get("software") or []
    return any(s.get("name") == title and s.get("installed_versions") for s in sw)


def changed_after(acts, since, grace_min=30, listed=lambda title: False):
    """A successful install or uninstall more than grace_min after `since`: the software list changed, and a host
    with working inventory would have reported it by now. An uninstall whose title the inventory still lists
    doesn't count: the uninstall left a copy behind and the list didn't change (860, 2026-11-26: Herdr's updater
    staged /Applications/Herdr.app.new.noindex, Fleet's uninstall removes only Herdr.app and reports success, and
    the soak's cleanup retried it every ~30 min, which looked like stuck inventory)."""
    cut = ts(since) + datetime.timedelta(minutes=grace_min)
    for a in acts:
        d = a.get("details") or {}
        if (a.get("type"), d.get("status")) not in SOFTWARE_DONE or ts(a["created_at"]) <= cut:
            continue
        if a.get("type") == "uninstalled_software" and d.get("software_title") and listed(d["software_title"]):
            continue
        return True
    return False


def stale_inventory(hosts, now, acts_of, listed_of=lambda hid: (lambda title: False)):
    """Online hosts whose software inventory is stuck, {host: software_updated_at}. Fleet only moves
    software_updated_at when the software list changes, so an old value alone means nothing (an idle host keeps
    it); stuck means it's STALE_MIN old although Fleet installed or uninstalled something on the host well after
    it. 2026-11-19: 858 stayed online and answered live queries and scripts, but its inventory didn't move for
    12 h through dozens of installs, so every app it tested looked undetected. If every such host is stuck, Fleet
    itself (or its ingestion) is the problem, not the hosts: do nothing."""
    cands = {hid: h["software_updated_at"] for hid, h in hosts.items()
             if h.get("status") == "online" and h.get("software_updated_at")
             and (now - ts(h["software_updated_at"])).total_seconds() / 60 >= STALE_MIN}
    if not cands:
        return {}
    stuck = {}
    for hid, since in cands.items():
        try:
            if changed_after(acts_of(hid), since, listed=listed_of(hid)):
                stuck[hid] = since
        except Exception as e:
            log(f"host {hid}: can't read activities ({e})")
    fresh = [hid for hid, h in hosts.items() if h.get("status") == "online" and h.get("software_updated_at")
             and (now - ts(h["software_updated_at"])).total_seconds() / 60 < STALE_MIN]
    return stuck if fresh else {}


def run_script(hid, platform, token):
    """Ask Fleet to restart fleetd on the host; True if Fleet accepted it."""
    body = json.dumps({"host_id": hid, "script_contents": RESTART_FLEETD["windows" if platform == "windows" else "darwin"]})
    req = urllib.request.Request(f"{BASE}/scripts/run", data=body.encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "User-Agent": "fleet-qa-vm-watchdog",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status < 300
    except Exception as e:
        log(f"host {hid}: fleetd restart script not accepted ({e})")
        return False


def fix_stale(hosts, state, now, token, local_names, mine):
    """Step through the fix for each stuck host this Mac owns (`mine`): restart fleetd through Fleet, then, if the
    inventory is still stuck FLEETD_WAIT_MIN later, restart the VM (if it's on this Mac) or post to Slack. State:
    state["stale"][host] = {"since": software_updated_at, "fleetd": iso, "vm": iso, "alerted": bool}."""
    stale = stale_inventory(hosts, now, lambda hid: activities(hid, token),
                            lambda hid: (lambda title: still_listed(hid, title, token)))
    book = dict(state.get("stale", {}))
    msgs = []
    for hid in list(book):
        if int(hid) not in stale:
            msgs.append(f"✅ FMA soak host {hid}: software inventory is updating again")
            book.pop(hid)
    for hid, since in stale.items():
        if hid not in mine:
            continue
        b = book.setdefault(str(hid), {"since": since})
        name = local_names.get(hid)
        if not b.get("fleetd"):
            log(f"host {hid}: online, software inventory stuck since {since}; {'would restart' if DRY else 'restarting'} fleetd")
            if not DRY and run_script(hid, hosts[hid].get("platform"), token):
                b["fleetd"] = now.isoformat()
                msgs.append(f"🔧 FMA soak host {hid}: software inventory stuck since {since[:16]}Z; restarted fleetd")
            continue
        waited = (now - ts(b["fleetd"])).total_seconds() / 60
        if waited < FLEETD_WAIT_MIN:
            continue
        if name and not b.get("vm"):
            log(f"host {hid} = VM '{name}': inventory still stuck {waited:.0f} min after the fleetd restart; "
                f"{'would restart' if DRY else 'restarting'} the VM")
            if not DRY:
                restart(name)
                b["vm"] = now.isoformat()
                msgs.append(f"🔧 FMA soak host {hid}: inventory still stuck after a fleetd restart; restarted its VM")
        elif not b.get("alerted"):
            b["alerted"] = True
            msgs.append(f"⚠️ FMA soak host {hid}: software inventory stuck since {since[:16]}Z, and a fleetd restart"
                        f"{' and a VM restart' if b.get('vm') else ''} didn't fix it. Pause it in FMA_SOAK_HOST_* and look.")
    return msgs, book


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
    local = {hid for hid, h in hosts.items() if named.get(str(hid)) or vms.get(mac(h.get("primary_mac")))}
    report = set(named.get("_alerts") or [hid for hid in hosts if hid not in local])
    msgs, alerted = outages({hid: h for hid, h in hosts.items() if hid in report}, state, now)
    for m in msgs:
        log(f"slack: {m}")
        if not DRY and not slack(m):
            return  # not sent: keep the old state so the next run tries again
    if not DRY and alerted != state.get("alerted", {}):
        state["alerted"] = alerted
        os.makedirs(CFG, exist_ok=True)
        json.dump(state, open(STATE, "w"))
    # stuck software inventory: this Mac handles its own VMs plus the hosts listed in vms.json "_stale"
    local_names = {hid: named.get(str(hid)) or vms.get(mac(h.get("primary_mac"))) for hid, h in hosts.items()}
    local_names = {hid: n for hid, n in local_names.items() if n}
    mine = set(local_names) | set(named.get("_stale") or [])
    smsgs, book = fix_stale(hosts, state, now, token, local_names, mine)
    for m in smsgs:
        log(f"slack: {m}")
        if not DRY:
            slack(m)
    if not DRY and book != state.get("stale", {}):
        state["stale"] = book
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
