"""Stuck-host watchdog, run beside hosts.py in the watch job every 30 min.

A host is stuck when the oldest activity in its queue has waited longer than stuck.minutes. On 2026-10-31 one
PrismaBrowser install hung `installer` on 631 and 636; fleetd's 1 h timeout killed only the script's shell (#57148),
the orphaned `installer` kept macOS's install lock, and every install queued behind it hung too: 18 h of findings
that were all the same hang. So, for a stuck host this:

  1. names the culprit: the oldest installer process left running on the host (oldest_holder; a live query),
     mapped to the soak record that was running when it started, and quarantines that app there
     (results/state/quarantine.json; runner.py merges it into known_environment, so cycles, samples and the update
     path skip it on that host). If the host can't answer, the culprit is unknown and nothing is quarantined;
  2. cancels what the harness queued there, then restarts the host through MDM (RestartDevice on macOS, Reboot/RebootNow on Windows) if it's in
     stuck.restart_hosts, at most stuck.max_restarts_per_day; a restart clears the orphaned installers;
  3. alerts (exit 1 with ::error:: lines; ci/alert.sh opens or comments on the "Soak host stuck" issue) when it
     finds a stuck host, and again when a restart didn't unstick it within stuck.recheck_minutes.

Results recorded while a host was stuck aren't evidence: digest.py counts them as environment (windows()).

  python3 stuck.py [--dry-run]   (--dry-run: report only; no restart, quarantine or state change)
"""
import base64, datetime, glob, json, os, re, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

PATH = os.path.join(R.WS, "results", "state", "stuck.json")
QUARANTINE = os.path.join(R.WS, "results", "state", "quarantine.json")


def iso(t):
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def local(t):
    return t.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def minutes(a, b):
    return (b - a).total_seconds() / 60


def queue_head(host):
    acts = R.upcoming(host)
    if acts is None:
        return None, None
    acts = [a for a in acts if a.get("created_at")]
    return (min(acts, key=lambda a: a["created_at"]), len(acts)) if acts else ({}, 0)


# The process holding the host: the oldest installer left running. On macOS a killed install script's `installer`
# is re-parented to launchd and keeps the system install lock (#57148); on Windows an msiexec that never returns holds
# the MSI mutex. Not "the first app that stalled": Defender stalls by itself (MDM approval) without blocking anyone,
# and dmg/zip apps keep passing while a pkg holds the lock, so neither records order nor a run of failures names it.
HOLDERS = "('installer', 'msiexec.exe')"


def oldest_holder(host, older_than_min):
    q = (f"SELECT pid, name, start_time, (strftime('%s','now') - start_time) AS age_s, substr(cmdline, 1, 200) AS cmd "
         f"FROM processes WHERE name IN {HOLDERS} AND (strftime('%s','now') - start_time) > {int(older_than_min * 60)} "
         f"ORDER BY start_time LIMIT 1;")
    st, d = R.api("POST", f"/hosts/{host}/query", {"query": q}, timeout=120)
    if st != 200 or d.get("error"):
        return None, f"live query failed ({st} {d.get('error') or d.get('message')})"
    rows = d.get("rows") or []
    return (rows[0], None) if rows else (None, "no installer process older than the limit")


def record_at(host, local_ts):
    """The soak record that was running on the host at local_ts (local naive ISO, like records' started)."""
    best = None
    for p in glob.glob(os.path.join(R.WS, "results", "soak", "*", f"*-{host}-*.json")):
        try:
            r = json.load(open(p))
        except (OSError, ValueError):
            continue
        if r.get("host") == host and r.get("started", "9") <= local_ts <= (r.get("finished") or "9999") \
                and (not best or r["started"] > best["started"]):
            best = r
    return best


def queued_app(host, plat, cmd):
    """The queued install whose package the holder is running (its record isn't written until the cycle ends)."""
    for a in R.upcoming(host) or []:
        d = a.get("details") or {}
        if d.get("software_package") and "/" + d["software_package"] in cmd:
            st, x = R.api("GET", f"/software/fleet_maintained_apps?fleet_id={R.FID}&per_page=2000")
            slug = next((f["slug"] for f in x.get("fleet_maintained_apps", [])
                         if f.get("name") == d.get("software_title") and f["slug"].endswith("/" + plat)), None)
            return slug and {"slug": slug, "started": local(R.utc(a["created_at"]))}
    return None


def culprit(host, limit, plat="darwin"):
    """(record, how) for the app holding the host, or (None, why not)."""
    proc, why = oldest_holder(host, limit)
    if not proc:
        return None, why
    t = local(datetime.datetime.fromtimestamp(int(proc["start_time"]), datetime.timezone.utc))
    rec = record_at(host, t) or queued_app(host, plat, proc["cmd"])
    how = f"{proc['name']} pid {proc['pid']} running {int(proc['age_s']) // 3600} h since {t}: {proc['cmd']}"
    return (rec, how) if rec else (None, f"{how}; no soak record or queued install matches it")


DRY = "--dry-run" in sys.argv


def quarantine(slug, host, why):
    if DRY:
        return f"{slug}@{host} (dry run: not written)"
    q = json.load(open(QUARANTINE)) if os.path.exists(QUARANTINE) else {}
    key = f"{slug}@{host}"
    if key not in q:
        q[key] = why
        os.makedirs(os.path.dirname(QUARANTINE), exist_ok=True)
        json.dump(q, open(QUARANTINE, "w"), indent=1)
    return key


def cancel_queue(host):
    """Take back what the harness queued on a stuck host before restarting it, so it comes back to an empty queue
    instead of re-running the install that hung it. Cancelling alone doesn't free the host: an activity already
    running there keeps running (its installer holds the lock until the restart). Fleet-initiated items are left."""
    if DRY:
        return "dry run: not cancelled"
    codes = [R.api("DELETE", f"/hosts/{host}/activities/upcoming/{a['uuid']}")[0]
             for a in R.upcoming(host) or [] if a.get("uuid") and not a.get("fleet_initiated")]
    return f"cancelled {sum(c < 300 for c in codes)} of {len(codes)}"


def restart_command(platform):
    cid = str(uuid.uuid4()).upper()
    if platform == "darwin":
        cmd = ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
               '"http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>Command</key><dict>'
               '<key>RequestType</key><string>RestartDevice</string></dict><key>CommandUUID</key>'
               f'<string>{cid}</string></dict></plist>')
    else:
        cmd = ('<Exec><CmdID>' + cid + '</CmdID><Item><Target><LocURI>./Device/Vendor/MSFT/Reboot/RebootNow</LocURI>'
               '</Target><Meta><Format xmlns="syncml:metinf">null</Format><Type>text/plain</Type></Meta><Data></Data>'
               '</Item></Exec>')
    return base64.b64encode(cmd.encode()).decode()


def restart(host):
    if DRY:
        return "dry run: not sent"
    st, d = R.api("GET", f"/hosts/{host}")
    h = d.get("host") or {}
    if not h.get("uuid"):
        return f"no host uuid ({st})"
    st, d = R.api("POST", "/commands/run", {"command": restart_command(h.get("platform")), "host_uuids": [h["uuid"]]})
    return "sent" if st < 300 else f"{st} {d.get('message')}"


def check():
    now = datetime.datetime.now(datetime.timezone.utc)
    cfg = R.CFG.get("stuck", {})
    limit, recheck = cfg.get("minutes", 75), cfg.get("recheck_minutes", 40)
    can_restart, per_day = set(cfg.get("restart_hosts", [])), cfg.get("max_restarts_per_day", 2)
    st = json.load(open(PATH)) if os.path.exists(PATH) else {}
    alerts = []
    for plat, hosts in R.CFG["hosts"].items():
        for host in hosts:
            s = st.setdefault(str(host), {"open": None, "history": []})
            snap = R.host_snapshot(host)
            if snap["status"] != "online":
                print(f"{plat} host {host}: {snap['status'] or 'no answer'}; hosts.py handles outages")
                continue
            head, n = queue_head(host)
            if head is None:
                print(f"{plat} host {host}: Fleet didn't answer the queue request; not judged this check")
                continue
            age = minutes(R.utc(head["created_at"]), now) if head else 0
            o = s["open"]
            if age < limit:
                if o:
                    o["until"], s["open"] = iso(now), None
                    s["history"] = (s["history"] + [o])[-100:]
                    print(f"{plat} host {host}: no longer stuck (was since {o['since']}"
                          + (f", restarted {o['restarted']}" if o.get("restarted") else "") + ")")
                else:
                    print(f"{plat} host {host}: queue moving ({n} queued, oldest {age:.0f} min)")
                continue
            what = (head.get("details") or {}).get("software_title") or head.get("type")
            if not o:
                bad, how = culprit(host, limit, plat)
                o = s["open"] = {"since": head["created_at"], "found": iso(now), "head": what,
                                 "window_start": bad["started"] if bad else local(R.utc(head["created_at"])),
                                 "culprit": bad and bad["slug"], "evidence": how}
                if bad:
                    o["quarantined"] = quarantine(bad["slug"], host,
                                                  f"held the host's install queue from {bad['started']} (stuck.py: {how}); "
                                                  f"remove this entry from results/state/quarantine.json to try it again")
                restarts = [e for e in s["history"] if e.get("restarted") and minutes(R.utc(e["restarted"]), now) < 1440]
                if host in can_restart and len(restarts) < per_day:
                    o["cancel_result"] = cancel_queue(host)
                    o["restart_result"] = restart(host)
                    o["restarted"] = iso(now) if o["restart_result"] == "sent" else None
                alerts.append(f"soak host {host} ({plat}) is stuck: '{what}' has waited {age:.0f} min in its queue "
                              f"({n} queued). Culprit: {o['culprit'] or 'unknown'} ({how})"
                              + (f" (quarantined on {host})" if o.get("quarantined") else "") + ". "
                              + (f"Queue: {o['cancel_result']}; restart through MDM: {o['restart_result']}." if "restart_result" in o
                                 else "Not restarted automatically (not in stuck.restart_hosts, or the day's restarts "
                                      "are used up): it needs a person."))
                print(f"{plat} host {host}: STUCK, {alerts[-1]}")
            elif o.get("restarted") and minutes(R.utc(o["restarted"]), now) >= recheck and not o.get("realerted"):
                o["realerted"] = iso(now)
                alerts.append(f"soak host {host} ({plat}) is still stuck {recheck} min after its MDM restart "
                              f"('{what}' queued since {head['created_at']}): it needs a person")
                print(f"{plat} host {host}: STILL STUCK after the restart")
            else:
                print(f"{plat} host {host}: stuck since {o['since']} (already handled)")
    if not DRY:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        json.dump(st, open(PATH, "w"), indent=1)
    for a in alerts:
        print(f"::error::{a}")
    return not alerts


def load():
    return json.load(open(PATH)) if os.path.exists(PATH) else {}


def windows(host):
    """(start, end, culprit) local naive ISO times the host was stuck; results started inside one, other than the
    culprit's, waited behind it and aren't evidence. An open window ends now."""
    s = load().get(str(host), {})
    out = []
    for e in s.get("history", []) + ([s["open"]] if s.get("open") else []):
        end = local(R.utc(e["until"])) if e.get("until") else local(datetime.datetime.now(datetime.timezone.utc))
        out.append((e.get("window_start") or local(R.utc(e["since"])), end, e.get("culprit")))
    return out


def blocked(rec):
    """The stuck window a record started in, if it wasn't the culprit's own cycle."""
    for start, end, bad in windows(rec.get("host")):
        if start <= rec.get("started", "") <= end and rec.get("slug") != bad:
            return start, end, bad
    return None


def for_date(date):
    out = []
    for host, s in load().items():
        for e in s.get("history", []) + ([s["open"]] if s.get("open") else []):
            if local(R.utc(e["since"]))[:10] <= date <= (local(R.utc(e["until"]))[:10] if e.get("until") else "9999"):
                out.append(f"- host {host}: queue stuck from {local(R.utc(e['since']))[5:16]} "
                           + (f"to {local(R.utc(e['until']))[5:16]}" if e.get("until") else "**still stuck**")
                           + f" behind {e.get('culprit') or e.get('head')}"
                           + (f", restarted through MDM at {local(R.utc(e['restarted']))[11:16]}" if e.get("restarted") else "")
                           + (", quarantined there" if e.get("quarantined") else ""))
    return out


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
