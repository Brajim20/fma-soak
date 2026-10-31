"""Patch when closed (S6, the gap 1 follow-up): Fleet's own patch policy with "patch when closed", on a real host.

How Fleet does it (937e87458c): turning on patch + patch_when_closed on a Fleet-maintained title (Edit software >
Deploy) creates a patch policy with an install automation and continuous automations. Each install the policy queues
uses the manifest's `open` query as its pre-install condition; that query returns a row only when the app is closed,
so while it's open the install is skipped and the host row reads failed_install + skipped_install. Continuous
automations re-fire on every failing result, at most once per policy update interval (~1 h), so after the app is
closed the patch can take up to an hour to land.

So the scenario spans passes, and the app it uses is locked (busy_slugs) so cycles, the update path and cleanup leave
it alone:
  start   pin the older cached version, install it, set Latest, open the app, turn on patch + patch when closed,
          refetch, wait for the first attempt (must be a skip, reported as one)
  open    each pass: the app must still be running on the old version (#53919: Chrome installed while open);
          after running_app.patch_closed.open_hours, close it and refetch
  closed  each pass: once the new version is on the host, the app must not have been reopened (#48638) and the
          patch policy must pass; after close_wait_hours without the patch, that's a finding
  end     turn patch off (Fleet deletes the policy), uninstall, release the lock, write the record

  python3 patch_closed.py tick --platform darwin    # what pass.sh runs: starts one a day, advances an active one
  python3 patch_closed.py status
  python3 patch_closed.py abort --platform darwin   # clean up an active one now
"""
import argparse, datetime, json, os, random, re, sys, time, urllib.error, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R
import running as RN

CFG = R.CFG.get("running_app", {}).get("patch_closed", {})
OPEN_HOURS = CFG.get("open_hours", 3)
CLOSE_WAIT_HOURS = CFG.get("close_wait_hours", 3)
FIRST_ATTEMPT_MINUTES = CFG.get("first_attempt_minutes", 20)


def path(plat):
    return R.patch_closed_path(plat)


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(t):
    return t.isoformat(timespec="seconds")


def hours_since(s):
    return (now() - datetime.datetime.fromisoformat(s)).total_seconds() / 3600


def load(plat):
    return json.load(open(path(plat))) if os.path.exists(path(plat)) else {"active": None, "history": []}


def save(st, plat):
    os.makedirs(os.path.dirname(path(plat)), exist_ok=True)
    json.dump(st, open(path(plat), "w"), indent=1)


def edit(tid, **fields):
    """Edit software (multipart, like the UI): patch / patch_when_closed are the Deploy options."""
    b = uuid.uuid4().hex
    parts = {"fleet_id": R.FID, **{k: str(v).lower() if isinstance(v, bool) else v for k, v in fields.items()}}
    body = "".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in parts.items())
    req = urllib.request.Request(f"{R.BASE}/software/titles/{tid}/package", method="PATCH",
                                 data=(body + f"--{b}--\r\n").encode(),
                                 headers={"Authorization": "Bearer " + R.TOKEN, "User-Agent": "fleet-qa-fma-soak",
                                          "Content-Type": "multipart/form-data; boundary=" + b})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": raw[:200].decode(errors="replace")}


def patch_policy(tid):
    st, d = R.api("GET", f"/fleets/{R.FID}/policies?per_page=500")
    return next((p for p in d.get("policies") or [] if (p.get("patch_software") or {}).get("software_title_id") == tid), None)


def host_policy(host, pid):
    st, d = R.api("GET", f"/hosts/{host}")
    p = next((x for x in (d.get("host") or {}).get("policies") or [] if x.get("id") == pid), None)
    return (p or {}).get("response")


def last_install(host, tid):
    sw = R.host_sw(host, tid) or {}
    li = (sw.get("software_package") or {}).get("last_install") or {}
    return {"uuid": li.get("install_uuid"), "status": sw.get("status"), "skipped_install": bool(sw.get("skipped_install")),
            "installed_versions": [v.get("version") for v in sw.get("installed_versions") or []]}


def candidates(plat, host):
    st, d = R.api("GET", f"/software/fleet_maintained_apps?fleet_id={R.FID}&per_page=2000")
    skip = set(R.CFG["canaries"][plat]) | {k.split("@")[0] for k in R.CFG.get("known_environment", {})} | \
        {x for xs in R.CFG.get("watcher", {}).get("residents", {}).values() for x in xs}
    out = []
    for x in d.get("fleet_maintained_apps", []):
        if not (x.get("software_title_id") and x["slug"].endswith("/" + plat)) or x["slug"] in skip:
            continue
        pkg = R.title_pkg(x["software_title_id"])
        if len(pkg.get("fleet_maintained_versions") or []) >= 2 and not pkg.get("pinned_version"):
            out.append(x["slug"])
    return sorted(out)


def record(a, outcome):
    rec = {"slug": a["slug"], "host": a["host"], "kind": "patch_when_closed", "build": R.build(),
           "started": a["started_local"], "findings": a["findings"], "outcome": outcome, "scenario": a}
    if a["findings"] and outcome == "pass":
        rec["outcome"] = "findings"
    return R.finish(rec)


def end(st, plat, outcome):
    """Cleanup (always): close the app, patch off (deletes the policy), uninstall, release the lock, record."""
    a = st["active"]
    host, tid, t = a["host"], a["title_id"], a.get("target")
    if t:
        RN.close_app(host, t, a, "end")
    a["patch_off"] = edit(tid, patch=False)[0]
    a["policy_after_patch_off"] = (patch_policy(tid) or {}).get("id")
    if a["policy_after_patch_off"]:
        a["findings"].append({"shape": "S4", "msg": "turning patch off left the patch policy in place"})
    exists_q = R.catalog_manifest(a["slug"])["versions"][0]["queries"]["exists"]
    R.uninstall(host, tid, a, "end_uninstall")
    hs = R.load_state(host)
    if R.present(host, exists_q) is False:
        hs["harness_installed"].pop(f"{a['slug']}@{host}", None)
        R.save_state(hs, host)
    a["ended"] = iso(now())
    rec = record(a, outcome)
    st["history"] = (st["history"] + [{"slug": a["slug"], "started": a["started"], "outcome": rec["outcome"],
                                       "record": rec["record"]}])[-60:]
    st["active"] = None
    save(st, plat)


def start(plat, st):
    host = R.CFG["hosts"][plat][0]
    done_today = [h for h in st["history"] if h["started"][:10] == iso(now())[:10]]
    if done_today:
        print(f"patch-closed {plat}: already ran today ({done_today[-1]['slug']}: {done_today[-1]['outcome']})")
        return
    used = {h["slug"] for h in st["history"]}
    pool = candidates(plat, host)
    fresh = [s for s in pool if s not in used] or pool
    if not fresh:
        print(f"patch-closed {plat}: no title with 2+ cached versions to use")
        return
    random.seed(iso(now())[:10] + plat)
    for slug in random.sample(fresh, min(5, len(fresh))):
        v = R.catalog_manifest(slug)["versions"][0]
        if not v["queries"].get("open"):
            continue
        tid, _ = R.ensure_title(slug, R.load_state(host), host)
        if patch_policy(tid) or R.present(host, v["queries"]["exists"]):
            continue
        break
    else:
        print(f"patch-closed {plat}: no usable title today")
        return
    if not R.queue_idle(host):
        print(f"patch-closed {plat}: host queue busy; trying next pass")
        return
    pkg = R.title_pkg(tid)
    cached = sorted({x["version"] for x in pkg.get("fleet_maintained_versions") or []},
                    key=lambda s: [int(p) for p in re.findall(r"\d+", s)])
    a = {"slug": slug, "host": host, "title_id": tid, "older": cached[-2], "newest": cached[-1], "phase": "start",
         "started": iso(now()), "started_local": datetime.datetime.now().isoformat(timespec="seconds"),
         "findings": [], "observations": []}
    st["active"] = a
    save(st, plat)  # the lock is on before anything is installed
    hs = R.load_state(host)
    hs["harness_installed"][f"{slug}@{host}"] = True
    R.save_state(hs, host)
    find = lambda shape, msg: a["findings"].append({"shape": shape, "msg": msg})
    exists_q = v["queries"]["exists"]
    try:
        a["pin_older"] = R.pin(tid, a["older"])
        R.install(host, tid, a, "old_install")
    finally:
        a["pin_latest"] = R.pin(tid, "")
    ctx = R.context(host, exists_q)
    a["context_old"] = ctx
    if a.get("old_install_status") != "installed" or not ctx:
        return end(st, plat, f"skipped: installing {a['older']} ended as {a.get('old_install_status')}")
    # the test needs the host behind: a hashless app can install newer bytes under the older version (#56873)
    if any(R.vcmp(x, a["newest"]) >= 0 for x in ctx.get("versions") or []):
        return end(st, plat, f"skipped: pinned {a['older']} but the host got {ctx.get('versions')} (#56873 shape)")
    t = RN.target(plat, ctx)
    if not t or not RN.open_app(host, t, a, "open"):
        a["target"] = t
        return end(st, plat, f"skipped: couldn't open the app ({a.get('open_open')})")
    a["target"], a["pids"] = t, a["open_open"]["pids"]
    a["before_uuid"] = last_install(host, tid)["uuid"]
    code, d = edit(tid, patch=True, patch_when_closed=True)
    a["patch_on"] = code
    pol = patch_policy(tid)
    if code >= 300 or not pol:
        find("S4", f"turning on patch + patch when closed returned {code} ({d.get('message')}); policy {pol and pol.get('id')}")
        return end(st, plat, "findings")
    a["policy"] = {k: pol.get(k) for k in ("id", "name", "patch_when_closed", "continuous_automations_enabled")}
    a["policy"]["install_title"] = (pol.get("install_software") or {}).get("software_title_id")
    if not (pol.get("patch_when_closed") and pol.get("continuous_automations_enabled") and a["policy"]["install_title"] == tid):
        find("S4", f"the patch policy Fleet created doesn't match the Deploy option: {a['policy']}")
    R.api("POST", f"/hosts/{host}/refetch")
    a["phase"], a["opened_at"] = "open", iso(now())
    save(st, plat)
    # first attempt: the refetch runs the policy now; a failing result fires the automation (no cooldown yet)
    t0 = time.time()
    while time.time() - t0 < FIRST_ATTEMPT_MINUTES * 60:
        time.sleep(60)
        li = last_install(host, tid)
        if li["uuid"] and li["uuid"] != a["before_uuid"] and li["status"] not in ("pending_install",):
            a["first_attempt"] = li
            break
    observe_open(st, plat)
    print(f"patch-closed {plat}: started {slug} ({a['older']} -> {a['newest']}), first attempt {a.get('first_attempt')}")


def observe_open(st, plat):
    """While the app is open: nothing may install, and a skip must be reported as a skip."""
    a = st["active"]
    host, tid, t = a["host"], a["title_id"], a["target"]
    find = lambda shape, msg: msg in [f["msg"] for f in a["findings"]] or a["findings"].append({"shape": shape, "msg": msg})
    li = last_install(host, tid)
    procs = RN.processes(host, t)
    ctx = R.context(host, R.catalog_manifest(a["slug"])["versions"][0]["queries"]["exists"])
    versions = (ctx or {}).get("versions") or []
    alive = [p for p in procs or [] if p["pid"] in a["pids"] and RN.current(p)]
    obs = {"at": iso(now()), "last_install": li, "versions": versions, "app_running": bool(alive),
           "policy": host_policy(host, a["policy"]["id"])}
    a["observations"].append(obs)
    upgraded = any(R.vcmp(v, a["newest"]) >= 0 for v in versions)
    if upgraded or li["status"] == "installed" and li["uuid"] != a["before_uuid"]:
        find("S6", f"patched to {a['newest']} while the app was open (patch when closed is on) (#53919 shape)")
    if li["uuid"] != a["before_uuid"] and li["status"] == "failed_install" and not li["skipped_install"]:
        find("S6", "a patch-when-closed attempt while the app was open is reported as a failure, not a skip (#52297 shape)")
    if procs is not None and not alive:
        a["note"] = "the app stopped running during the open phase (host restart or the app quit on its own)"
    save(st, plat)
    return obs


def observe_closed(st, plat):
    a = st["active"]
    host, tid = a["host"], a["title_id"]
    find = lambda shape, msg: a["findings"].append({"shape": shape, "msg": msg})
    v = R.catalog_manifest(a["slug"])["versions"][0]
    ctx = R.context(host, v["queries"]["exists"])
    versions = (ctx or {}).get("versions") or []
    li = last_install(host, tid)
    obs = {"at": iso(now()), "last_install": li, "versions": versions, "policy": host_policy(host, a["policy"]["id"])}
    a["observations"].append(obs)
    if any(R.vcmp(x, a["newest"]) >= 0 for x in versions):
        a["patched_after_close_hours"] = round(hours_since(a["closed_at"]), 2)
        t2 = RN.target(plat, ctx) or a["target"]
        if plat == "windows":
            RN.find_exe(host, t2)
        RN.judge_closed(a, find, "patched", f"patch to {a['newest']} after the app was closed", RN.processes(host, t2))
        patched_q = v["queries"].get("patched", "").replace(f"'{v['version']}'", f"'{a['newest']}'")
        a["patched_query_passes"] = R.present(host, patched_q) if patched_q else None
        if a["patched_query_passes"] is False:
            find("S1", f"after the patch to {a['newest']} the patched query still fails")
        a["target"] = t2
        return end(st, plat, "pass")
    if hours_since(a["closed_at"]) >= CLOSE_WAIT_HOURS:
        find("S4", f"the app was closed {CLOSE_WAIT_HOURS} h ago and the patch to {a['newest']} never installed "
                   f"(last attempt: {li['status']}, policy {obs['policy']})")
        return end(st, plat, "findings")
    save(st, plat)


def tick(plat):
    st = load(plat)
    a = st["active"]
    if not a:
        return start(plat, st)
    if not R.wait_online(a["host"], minutes=5):
        print(f"patch-closed {plat}: host offline; {a['slug']} stays in phase {a['phase']}")
        return
    if a["phase"] == "start":  # interrupted before the patch was turned on
        return end(st, plat, "harness: interrupted during setup")
    if a["phase"] == "open":
        observe_open(st, plat)
        if hours_since(a["opened_at"]) >= OPEN_HOURS:
            RN.close_app(a["host"], a["target"], a, "close")
            R.api("POST", f"/hosts/{a['host']}/refetch")
            a["phase"], a["closed_at"] = "closed", iso(now())
            save(st, plat)
            print(f"patch-closed {plat}: {a['slug']} closed after {hours_since(a['opened_at']):.1f} h open")
        else:
            print(f"patch-closed {plat}: {a['slug']} open {hours_since(a['opened_at']):.1f} h; last {a['observations'][-1]['last_install']}")
        return
    if a["phase"] == "closed":
        observe_closed(st, plat)
        a = st["active"]
        print(f"patch-closed {plat}: " + (f"{a['slug']} closed {hours_since(a['closed_at']):.1f} h, waiting for the patch"
                                          if a else "finished"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["tick", "status", "abort"])
    ap.add_argument("--platform", choices=["darwin", "windows"])
    x = ap.parse_args()
    plats = [x.platform] if x.platform else ["darwin", "windows"]
    for p in plats:
        if x.action == "tick":
            tick(p)
        elif x.action == "status":
            st = load(p)
            print(p, json.dumps({k: (st["active"] or {}).get(k) for k in ("slug", "phase", "opened_at", "closed_at", "findings")}),
                  f"history {len(st['history'])}")
        elif x.action == "abort" and load(p)["active"]:
            end(load(p), p, "aborted")


if __name__ == "__main__":
    main()
