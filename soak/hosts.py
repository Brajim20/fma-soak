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
    opens = [s["open"] for s in st.values() if s.get("open")]
    if alerts and len(opens) == len(st) > 1 and together([o["since"] for o in opens]):
        # every soak VM dropped at once: the Mac running them, not the VMs
        alerts = [f"all soak hosts went offline together around {min(o['since'] for o in opens)}: the Mac running the "
                  f"VMs restarted, slept or lost its network. It's managed with FileVault, so after a restart someone has "
                  f"to log in; the VMs then start by themselves"]
    for a in alerts:
        print(f"::error::{a}")
    return not alerts


def together(times, minutes=15):
    ts = sorted(R.utc(t) for t in times)
    return len(ts) > 1 and (ts[-1] - ts[0]).total_seconds() <= minutes * 60


def outages(host):
    """(since, until) in UTC for a host, the open one ending now."""
    if not os.path.exists(PATH):
        return []
    s = json.load(open(PATH)).get(str(host), {})
    out = [(R.utc(e["since"]), R.utc(e["until"])) for e in s.get("history", [])]
    if s.get("open"):
        out.append((R.utc(s["open"]["since"]), datetime.datetime.now(datetime.timezone.utc)))
    return out


def offline_hours(host, start, end):
    """Hours of [start, end) the host was offline (aware datetimes). Outages are closed when a check finds the host
    back, so this can overcount by up to one check interval; it errs toward not blaming an app for the downtime."""
    return sum(max(0, (min(b, end) - max(a, start)).total_seconds()) for a, b in outages(host)) / 3600


def for_date(date):
    """Digest lines: outages and restarts that touched this local date, and any outage still open."""
    if not os.path.exists(PATH):
        return []
    local = lambda ts: R.utc(ts).astimezone()
    fmt = lambda ts: local(ts).strftime("%m-%d %H:%M")
    out = []
    st = json.load(open(PATH))
    starts = {h: [e["since"] for e in s.get("history", []) if e["since"] != e["until"]]
              + ([s["open"]["since"]] if s.get("open") else []) for h, s in st.items()}
    # an outage another host shares (started within 15 min) was the Mac running the VMs
    shared = lambda h, t: any(together([t, u]) for o, us in starts.items() if o != h for u in us)
    for host, s in st.items():
        for e in s.get("history", []):
            if local(e["since"]).date().isoformat() <= date <= local(e["until"]).date().isoformat():
                if e["since"] == e["until"]:
                    out.append(f"- host {host}: restarted at {fmt(e['since'])} and came back on its own")
                else:
                    h = hours(R.utc(e["since"]), R.utc(e["until"]))
                    out.append(f"- host {host}: offline {fmt(e['since'])} → {fmt(e['until'])} ({h:.1f} h)"
                               + (" (restarted)" if e.get("restarted") else "")
                               + (": the VMs' Mac" if shared(host, e["since"]) else ""))
        if s.get("open"):
            out.append(f"- host {host}: **offline since {fmt(s['open']['since'])}, still down at the last check**"
                       + (" (the VMs' Mac)" if shared(host, s["open"]["since"]) else ""))
    return out


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
