"""Soak-host availability, checked by the watcher job every 2 h (task 4.9).

The soak VMs run on a managed Mac with FileVault, so after any restart they stay down until someone logs in. This
keeps results/state/outages.json (each host's open outage and its history, for the digest) and exits 1 once a host
has been offline for loop.alert_offline_hours: the scheduled run fails, and GitHub emails the repo owner. It alerts
once, then again every loop.realert_hours while the outage lasts. Restarts the host came back from on its own between
two checks are recorded too.

  python3 hosts.py
"""
import datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

PATH = os.path.join(R.WS, "results", "state", "outages.json")


def iso(t):
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def hours(a, b):
    return (b - a).total_seconds() / 3600


def check():
    now = datetime.datetime.now(datetime.timezone.utc)
    loop = R.CFG.get("loop", {})
    alert_h, realert_h = loop.get("alert_offline_hours", 2), loop.get("realert_hours", 12)
    st = json.load(open(PATH)) if os.path.exists(PATH) else {}
    alerts = []
    for plat, hosts in R.CFG["hosts"].items():
        for host in hosts:
            s = st.setdefault(str(host), {"open": None, "history": [], "last_restarted": None})
            snap = R.host_snapshot(host)
            if not snap["status"]:
                print(f"{plat} host {host}: Fleet didn't answer; not judged this check")
                continue
            if snap["status"] != "online":
                o = s["open"] = s["open"] or {"since": snap["seen"], "found": iso(now)}
                h = hours(R.utc(o["since"]), now)
                print(f"{plat} host {host}: {snap['status']} since {o['since']} ({h:.1f} h)")
                if h >= alert_h and (not o.get("alerted") or hours(R.utc(o["alerted"]), now) >= realert_h):
                    o["alerted"] = iso(now)
                    alerts.append(f"soak host {host} ({plat}) has been offline for {h:.1f} h (last seen {o['since']}); "
                                  f"its VM or the Mac running it needs a person (FileVault: log in after a restart)")
                continue
            r = snap["restarted"]
            if s["open"]:
                o, s["open"] = s["open"], None
                came_back_restarted = bool(r and R.utc(r) > R.utc(o["since"]))
                s["history"].append({"since": o["since"], "until": iso(now), "restarted": came_back_restarted})
                print(f"{plat} host {host}: back online; was down from {o['since']}"
                      + (f", restarted at {r}" if came_back_restarted else ""))
            elif r and s["last_restarted"] and abs(hours(R.utc(s["last_restarted"]), R.utc(r))) > 5 / 60:
                # restarted and came back between two checks; last_restarted_at jitters by seconds, so allow 5 min
                s["history"].append({"since": r, "until": r, "restarted": True})
                print(f"{plat} host {host}: online, restarted at {r} since the last check")
            else:
                print(f"{plat} host {host}: online")
            if r:
                s["last_restarted"] = r
            s["history"] = s["history"][-200:]
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    json.dump(st, open(PATH, "w"), indent=1)
    for a in alerts:
        print(f"::error::{a}")
    return not alerts


def for_date(date):
    """Digest lines: outages and restarts that touched this local date, and any outage still open."""
    if not os.path.exists(PATH):
        return []
    local = lambda ts: R.utc(ts).astimezone()
    fmt = lambda ts: local(ts).strftime("%m-%d %H:%M")
    out = []
    for host, s in json.load(open(PATH)).items():
        for e in s.get("history", []):
            if local(e["since"]).date().isoformat() <= date <= local(e["until"]).date().isoformat():
                if e["since"] == e["until"]:
                    out.append(f"- host {host}: restarted at {fmt(e['since'])} and came back on its own")
                else:
                    out.append(f"- host {host}: offline {fmt(e['since'])} → {fmt(e['until'])}"
                               + (" (restarted)" if e.get("restarted") else ""))
        if s.get("open"):
            out.append(f"- host {host}: **offline since {fmt(s['open']['since'])}, still down at the last check**")
    return out


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
