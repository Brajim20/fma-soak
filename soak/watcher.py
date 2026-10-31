"""Task 4.3 (L3): upstream transition watcher for #55961.

How Fleet moves a host to a new FMA version on its own (verified in source, 2026-10-30):
  1. the hourly `maintained_apps_auto_update` cron downloads the new manifest version and promotes it to the
     active installer for each fleet (unless the title is pinned);
  2. promotion regenerates the patch policy's query from the new installer and resets the policy;
  3. hosts on the old version now fail the patch policy; its install automation queues the new installer;
  4. the host installs it and inventory reports the new version.
Nothing else pushes a new version to hosts.

Residents (config `watcher.residents`: frequent updaters with small installers) stay installed on the soak hosts,
each with a harness-owned patch policy in the soak fleet that has an install automation. Every pass, `check` records an
observation per resident and follows each transition (Fleet's active version changes) through those steps.
A transition that reaches the host is written as a passing record (kind "transition", with its timeline); one
that hasn't after `window_hours` is written once as an S4 finding naming the step it stopped at. Both show up in
the digest like any other record.

Residents are excluded from canaries, the sample and the update path; the harness never removes them.

  python3 watcher.py setup [--platform P]    # add titles, create the patch policies, install residents (idempotent)
  python3 watcher.py check --platform P      # one observation round (pass.sh runs this every pass)
  python3 watcher.py status
"""
import argparse, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

OUT = os.path.join(R.WS, "results", "transitions")
WCFG = {"window_hours": 24, "main_lag_hours": 72, "residents": {}, **R.CFG.get("watcher", {})}
POLICY_PREFIX = "FMA soak resident: "


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def hours_since(ts):
    return (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600


def online_hours_since(host, ts):
    """Hours since ts that the host was online: a release can't reach a host that's down (or whose Mac is)."""
    import hosts
    start = datetime.datetime.fromisoformat(ts).astimezone()
    end = datetime.datetime.now().astimezone()
    return (end - start).total_seconds() / 3600 - hosts.offline_hours(host, start, end)


def state_path(plat):
    # one file per platform: the macOS and Windows passes run side by side
    return os.path.join(OUT, f"residents-{plat}.json")


def load(plat):
    p = state_path(plat)
    return json.load(open(p)) if os.path.exists(p) else {}


def save(st, plat):
    os.makedirs(OUT, exist_ok=True)
    json.dump(st, open(state_path(plat), "w"), indent=1)


def residents(plat):
    for slug in WCFG["residents"].get(plat, []):
        yield slug, R.CFG["hosts"][plat][0]


def resident_slugs():
    return {s for slugs in WCFG["residents"].values() for s in slugs}


def find_policy(slug):
    """Harness-owned patch policies are found by their name; nothing else in the soak fleet is touched."""
    st, d = R.api("GET", f"/fleets/{R.FID}/policies?per_page=500")
    return next((p for p in d.get("policies") or [] if p.get("name") == f"{POLICY_PREFIX}{slug}"), None)


def setup(plat):
    st = load(plat)
    for slug, host in residents(plat):
        key = f"{slug}@{host}"
        s = st.setdefault(key, {"slug": slug, "host": host, "since": now(), "transitions": []})
        hstate = R.load_state(host)
        tid, added = R.ensure_title(slug, hstate, host)
        s["title_id"] = tid
        pol = find_policy(slug)
        if not pol:
            code, d = R.api("POST", f"/fleets/{R.FID}/policies",
                            {"type": "patch", "patch_software_title_id": tid, "software_title_id": tid,
                             "name": f"{POLICY_PREFIX}{slug}"})
            if code >= 300:
                print(f"{key}: patch policy create failed {code}: {d.get('message')}")
                save(st, plat)
                continue
            pol = d.get("policy") or {}
        s["policy_id"] = pol.get("id")
        exists_q = R.catalog_manifest(slug)["versions"][0]["queries"]["exists"]
        if not R.present(host, exists_q):
            rec = {}
            R.install(host, tid, rec, "resident_install")
            s["installed"] = {"status": rec.get("resident_install_status"), "at": now()}
        s["fleet_version"] = R.title_pkg(tid).get("version")
        print(f"{key}: title {tid}, policy {s['policy_id']}, Fleet active {s['fleet_version']}, "
              f"install {s.get('installed', {}).get('status', 'already present')}")
    save(st, plat)


def host_policy(host, pid):
    st, d = R.api("GET", f"/hosts/{host}")
    p = next((x for x in (d.get("host") or {}).get("policies") or [] if x.get("id") == pid), None)
    return (p or {}).get("response")


def upcoming_for(host, tid):
    st, d = R.api("GET", f"/hosts/{host}/activities/upcoming?per_page=100")
    return [a.get("type") for a in d.get("activities") or [] if (a.get("details") or {}).get("software_title_id") == tid]


def write_record(s, t, outcome, findings):
    """Same shape as a cycle record, so the ledger and digest pick it up."""
    started = t["promoted_seen"]
    rec = {"slug": s["slug"], "host": s["host"], "kind": "transition", "build": R.build(), "started": started,
           "finished": now(), "catalog_version": t["to"], "transition": t, "outcome": outcome, "findings": findings}
    d = os.path.join(R.WS, "results", "soak", now()[:10])
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{s['slug'].replace('/', '_')}-{s['host']}-transition-{t['to']}.json")
    rec["record"] = p
    json.dump(rec, open(p, "w"), indent=1)
    print(f"   transition record: {outcome} {[f['msg'] for f in findings]}")


def check(plat):
    st = load(plat)
    for key, s in st.items():
        if not s.get("title_id"):
            continue
        host, tid, slug = s["host"], s["title_id"], s["slug"]
        man = R.catalog_manifest(slug)["versions"][0]
        fv = R.title_pkg(tid).get("version")
        ctx = R.context(host, man["queries"]["exists"]) or {}
        obs = {"at": now(), "build": R.build(), "main_version": man["version"], "fleet_version": fv,
               "host_versions": ctx.get("versions"), "policy": host_policy(host, s.get("policy_id")),
               "upcoming": upcoming_for(host, tid)}
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f"{slug.replace('/', '_')}@{host}.jsonl"), "a") as f:
            f.write(json.dumps(obs) + "\n")
        host_txt = obs["host_versions"] if ctx else "unknown (live query returned nothing; not judged this check)"
        print(f"{key}: main {obs['main_version']}, Fleet {fv}, host {host_txt}, policy {obs['policy']}, upcoming {obs['upcoming']}")

        # main ahead of Fleet: the published manifest lags main, so only a long gap is worth a look
        if R.vcmp(man["version"], fv or "0") > 0:
            s.setdefault("main_ahead_since", obs["at"])
            if hours_since(s["main_ahead_since"]) > WCFG["main_lag_hours"] and not s.get("main_lag_reported"):
                write_record(s, {"from": fv, "to": man["version"], "promoted_seen": s["main_ahead_since"]}, "findings",
                             [{"shape": "S4", "msg": f"main has {man['version']} for {WCFG['main_lag_hours']}+ h but Fleet's active version is still {fv}"}])
                s["main_lag_reported"] = True
        else:
            s.pop("main_ahead_since", None); s.pop("main_lag_reported", None)

        open_t = next((t for t in s["transitions"] if not t.get("closed")), None)
        if fv and s.get("fleet_version") and fv != s["fleet_version"]:
            if open_t:  # superseded before it reached the host
                open_t.update(closed=obs["at"], result=f"superseded by {fv}")
            open_t = {"from": s["fleet_version"], "to": fv, "promoted_seen": obs["at"]}
            s["transitions"].append(open_t)
            print(f"   new transition {open_t['from']} -> {fv}")
        s["fleet_version"] = fv or s.get("fleet_version")
        if not open_t:
            continue
        if obs["policy"] == "fail":
            open_t.setdefault("policy_failed_seen", obs["at"])
        if "installed_software" in obs["upcoming"]:
            open_t.setdefault("install_queued_seen", obs["at"])
        hv = obs["host_versions"] or []
        if hv and any(R.vcmp(v, open_t["to"]) >= 0 for v in hv):
            open_t.update(host_reached=obs["at"], closed=obs["at"], result="reached")
            findings = []
            if obs["policy"] == "fail":
                findings.append({"shape": "S1", "msg": f"host has {hv} but the patch policy still fails"})
            write_record(s, open_t, "pass" if not findings else "findings", findings)
        elif ctx and online_hours_since(host, open_t["promoted_seen"]) > WCFG["window_hours"] and not open_t.get("reported_stuck"):
            # only call it stuck on a check that actually read the host; a failed live query says nothing
            stage = ("the patch policy never failed on the host" if "policy_failed_seen" not in open_t else
                     "the policy failed but no install was queued" if "install_queued_seen" not in open_t else
                     "an install was queued but the host never reported the new version")
            write_record(s, open_t, "findings", [{"shape": "S4", "msg":
                         f"{open_t['from']} -> {open_t['to']} not on the host after {WCFG['window_hours']} h: {stage} (host has {hv})"}])
            open_t["reported_stuck"] = True
    save(st, plat)


def status(plat):
    for key, s in load(plat).items():
        print(key, {k: s.get(k) for k in ("title_id", "policy_id", "fleet_version")})
        for t in s["transitions"]:
            print("   ", t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "check", "status"])
    ap.add_argument("--platform", choices=["darwin", "windows"])
    a = ap.parse_args()
    for plat in [a.platform] if a.platform else list(WCFG["residents"]):
        {"setup": setup, "check": check, "status": status}[a.cmd](plat)


if __name__ == "__main__":
    main()
