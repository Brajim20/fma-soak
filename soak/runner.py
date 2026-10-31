#!/usr/bin/env python3
"""L2 lifecycle soak runner (#55961, tasks 3.5, 3.6, 3.8) on the soak hosts in the soak fleet.

One cycle for one app on one host:
  1. make sure the FMA is available in the soak fleet (adds it if needed; remembers it as harness-owned)
  2. pre-check: the app must NOT already be on the host (its catalog `exists` query) -> otherwise skip;
     apps the harness didn't install are never touched
  3. install through Fleet          -> Fleet status vs presence on the host (S3), install output
  4. inventory after a refetch      -> reported version has the same shape as the FMA version (S1)
  5. uninstall through Fleet        -> status vs absence on the host (S3)
  6. reinstall + uninstall again    -> a host that had the app before behaves the same
Every step is recorded; the cycle JSON goes to results/soak/<date>/.

  python3 runner.py <slug> [--host <id>]     one cycle (host defaults to the platform's soak host)
  python3 runner.py --canaries [darwin|windows]
"""
import argparse, datetime, json, os, re, subprocess, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, WS)
import settings
CFG = settings.load()
REPO = settings.catalog_repo(CFG)  # CI: a sparse checkout
FID = CFG["soak_fleet_id"]
POLL, STEP_TIMEOUT = 15, 1800


def ctx_token(ctx):
    import yaml
    return yaml.safe_load(open(os.path.expanduser("~/.fleet/config")))["contexts"][ctx]["token"]


def token():
    # CI (task 4.9) passes the token as a secret env var; locally it comes from the fleetctl context
    return os.environ.get("FMA_SOAK_TOKEN") or ctx_token(CFG.get("fleetctl_context", "default"))


TOKEN = token()
BASE = CFG["instance"].rstrip("/") + "/api/latest/fleet"


def api(method, path, body=None, timeout=120, tries=5):
    """HTTP errors are returned as (status, body). Connection errors on this side (DNS, resets, timeouts)
    are retried; after the last try they come back as status 0 so a cycle records them instead of crashing."""
    for i in range(tries):
        req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + TOKEN, "User-Agent": "fleet-qa-fma-soak", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"message": raw[:300].decode(errors="replace")}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(15 * (i + 1))
    return 0, {"message": f"connection failed after {tries} tries: {err}"}


def state_path(host):
    # one file per host: the macOS and Windows runs execute in parallel. Under results/state/ so CI can save and
    # restore all harness state as one folder (task 4.9).
    return os.path.join(WS, "results", "state", f"state-{host}.json")


def load_state(host):
    p = state_path(host)
    old = os.path.join(HERE, f"state-{host}.json")  # pre-4.9 location, read until the first save moves it
    for f in (p, old):
        if os.path.exists(f):
            return json.load(open(f))
    return {"harness_titles": {}, "harness_installed": {}}


def save_state(s, host):
    os.makedirs(os.path.dirname(state_path(host)), exist_ok=True)
    json.dump(s, open(state_path(host), "w"), indent=1)


def catalog_manifest(slug):
    base, plat = slug.split("/")
    out = subprocess.run(["git", "-C", REPO, "show", f"origin/main:ee/maintained-apps/outputs/{base}/{plat}.json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def build():
    with urllib.request.urlopen(urllib.request.Request(CFG["instance"].rstrip("/") + "/version", headers={"User-Agent": "fleet-qa"}), timeout=30) as r:
        v = json.load(r)
    return f"{v['version']} ({v['revision'][:10]})"


PRESENCE_ERRORS = []


def present(host, exists_query, tries=4):
    """True/False from the app's catalog `exists` query; None if the host never answered.
    Right after an install or uninstall the host is busy, so retry before giving up."""
    for i in range(tries):
        st, d = api("POST", f"/hosts/{host}/query", {"query": exists_query}, timeout=180)
        if st == 200 and not d.get("error"):
            return len(d.get("rows") or []) > 0
        PRESENCE_ERRORS.append(f"{st} {d.get('error') or d.get('message')}"[:160])
        time.sleep(20)
    return None


def host_sw(host, title_id):
    st, d = api("GET", f"/hosts/{host}/software?available_for_install=true&per_page=500&{'fleet_id'}={FID}")
    return next((s for s in d.get("software", []) if s.get("id") == title_id), None)


def host_snapshot(host):
    st, d = api("GET", f"/hosts/{host}")
    h = d.get("host") or {}
    return {"status": h.get("status"), "seen": h.get("seen_time"), "restarted": h.get("last_restarted_at")}


def utc(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


# times a cycle's polling found its host offline; finish() uses it to tell a disrupted cycle from a real finding
HOST_OFFLINE_SEEN = []


def start_watch(host):
    HOST_OFFLINE_SEEN.clear()
    return host_snapshot(host)


def disruption(rec):
    """Why the host can't be trusted for this cycle: it restarted (Fleet's last_restarted_at moved; it's computed from
    uptime, so allow some jitter) or was seen offline. The VMs' Mac is managed with FileVault, so a restart leaves
    them down until someone logs in."""
    a, b = rec.get("host_at_start") or {}, rec.get("host_at_end") or {}
    why = []
    if a.get("restarted") and b.get("restarted") and abs((utc(b["restarted"]) - utc(a["restarted"])).total_seconds()) > 300:
        why.append(f"restarted at {b['restarted']}")
    if HOST_OFFLINE_SEEN:
        why.append(f"offline at {HOST_OFFLINE_SEEN[0]}")
    if b.get("status") and b["status"] != "online":
        why.append(f"{b['status']} when the cycle ended")
    return why


def wait_status(host, title_id, pending, timeout=STEP_TIMEOUT):
    t0 = time.time()
    # first wait (briefly) for Fleet to show the request as pending; polling straight away can still see the
    # previous status ("installed" right after an uninstall request) and stop too early
    while time.time() - t0 < 90:
        s = host_sw(host, title_id)
        if s and s.get("status") == pending:
            break
        time.sleep(5)
    n = 0
    while time.time() - t0 < timeout:
        s = host_sw(host, title_id)
        if s and s.get("status") != pending:
            return s, round(time.time() - t0)
        n += 1
        if n % 20 == 0 and host_snapshot(host)["status"] != "online":  # every ~5 min
            HOST_OFFLINE_SEEN.append(datetime.datetime.now().isoformat(timespec="seconds"))
        time.sleep(POLL)
    return host_sw(host, title_id), None


def upcoming(host):
    st, d = api("GET", f"/hosts/{host}/activities/upcoming?per_page=100")
    return d.get("activities") or [] if st == 200 else None


def queue_idle(host, timeout=STEP_TIMEOUT):
    """Wait for the host's queue to empty before asking for anything. Fleet runs a host's activities one at a time,
    so a request behind a slow one (a big download on the instance's slow link) only times out here and piles up:
    on 2026-10-31 canaries left 14 installs queued on 631 that way."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if upcoming(host) == []:
            return True
        time.sleep(POLL * 4)
    return False


def cancel_pending(host, title_id):
    """After a step times out, take back what it queued, so it doesn't run later untracked."""
    codes = []
    for a in upcoming(host) or []:
        if (a.get("details") or {}).get("software_title_id") == title_id and not a.get("fleet_initiated"):
            codes.append(api("DELETE", f"/hosts/{host}/activities/upcoming/{a['uuid']}")[0])
    return codes


def ensure_title(slug, state, host):
    st, d = api("GET", f"/software/fleet_maintained_apps?fleet_id={FID}&per_page=2000")
    row = next((a for a in d.get("fleet_maintained_apps", []) if a["slug"] == slug), None)
    if not row:
        raise RuntimeError(f"{slug} not in the FMA catalog on this instance")
    if row.get("software_title_id"):
        return row["software_title_id"], False
    st, d = api("POST", "/software/fleet_maintained_apps", {"fleet_maintained_app_id": row["id"], "fleet_id": FID}, timeout=900)
    if st >= 300:
        raise RuntimeError(f"add failed {st}: {d.get('message')}")
    state["harness_titles"][slug] = d["software_title_id"]
    save_state(state, host)
    return d["software_title_id"], True


# Error text that means the install script really failed, even if Fleet says "installed" and the app is on disk
# (#51123: "Success" followed by `realpath: ... No such file` and `hdiutil: attach failed`). Kept to specific
# phrases: MSI verbose logs and plist dumps are full of the bare words "error" and "failed".
ERROR_MARKERS = re.compile(r"realpath: .*No such file|hdiutil: .*failed|image not recognised|Error running|"
                           r"script execution error|exit status [1-9]|command not found|Permission denied|"
                           r"cannot open|is damaged|No space left", re.I)


def vcmp(a, b):
    """Numeric compare; trailing zeros don't matter (2.42304.2.0 == 2.42304.2)."""
    pa, pb = [int(x) for x in re.findall(r"\d+", str(a))], [int(x) for x in re.findall(r"\d+", str(b))]
    n = max(len(pa), len(pb))
    pa, pb = pa + [0] * (n - len(pa)), pb + [0] * (n - len(pb))
    return (pa > pb) - (pa < pb)


def vshape(v):
    return len([p for p in re.split(r"[.\-_ ]", str(v)) if p])


def last_result_id(sw, kind):
    pkg = (sw or {}).get("software_package") or {}
    return ((pkg.get("last_install") or {}).get("install_uuid") if kind == "install"
            else (pkg.get("last_uninstall") or {}).get("script_execution_id"))


def not_run(host, tid, rec, label, kind, before):
    """A request that was cancelled (or never ran) leaves the previous result in place, and Fleet goes back to the
    previous status: 2026-10-31 an upgrade cancelled mid-wait read as "installed" with the old version's output.
    Only a new result counts."""
    s = host_sw(host, tid)
    if rec.get(f"{label}_status") and rec[f"{label}_status"] != f"pending_{kind}" and last_result_id(s, kind) == before:
        rec[f"{label}_status"] = f"not_run (no new {kind} result; status was {rec[f'{label}_status']})"
        return True
    return False


def install(host, tid, rec, label):
    before = last_result_id(host_sw(host, tid), "install")
    st, d = api("POST", f"/hosts/{host}/software/{tid}/install")
    rec[f"{label}_request"] = st
    if st >= 300:
        rec[f"{label}_error"] = d.get("message")
        return None
    s, secs = wait_status(host, tid, "pending_install")
    rec[f"{label}_status"], rec[f"{label}_seconds"] = (s or {}).get("status"), secs
    if secs is None and rec[f"{label}_status"] == "pending_install":
        rec[f"{label}_cancelled_after_timeout"] = cancel_pending(host, tid)
    if not_run(host, tid, rec, label, "install", before):
        return s
    uuid = (((s or {}).get("software_package") or {}).get("last_install") or {}).get("install_uuid")
    if uuid:
        st, r = api("GET", f"/software/install/{uuid}/results")
        res = r.get("results", {})
        out = res.get("output") or ""
        rec[f"{label}_output_tail"] = scrub(out[-600:])
        rec[f"{label}_error_markers"] = sorted({m.group(0)[:80] for m in ERROR_MARKERS.finditer(out)})
    return s


def uninstall(host, tid, rec, label):
    before = last_result_id(host_sw(host, tid), "uninstall")
    st, d = api("POST", f"/hosts/{host}/software/{tid}/uninstall")
    rec[f"{label}_request"] = st
    if st >= 300:
        rec[f"{label}_error"] = d.get("message")
        return None
    s, secs = wait_status(host, tid, "pending_uninstall")
    rec[f"{label}_status"], rec[f"{label}_seconds"] = (s or {}).get("status"), secs
    if secs is None and rec[f"{label}_status"] == "pending_uninstall":
        rec[f"{label}_cancelled_after_timeout"] = cancel_pending(host, tid)
    if not_run(host, tid, rec, label, "uninstall", before):
        return s
    eid = (((s or {}).get("software_package") or {}).get("last_uninstall") or {}).get("script_execution_id")
    if eid:
        st, r = api("GET", f"/scripts/results/{eid}")
        rec[f"{label}_output_tail"] = scrub((r.get("output") or "")[-600:])
        rec[f"{label}_exit_code"] = r.get("exit_code")
    return s


def cycle(slug, host):
    state = load_state(host)
    man = catalog_manifest(slug)
    v = man["versions"][0]
    exists_q = v["queries"]["exists"]
    rec = {"slug": slug, "host": host, "build": build(), "started": datetime.datetime.now().isoformat(timespec="seconds"),
           "catalog_version": v["version"], "findings": [], "host_at_start": start_watch(host)}
    def find(shape, msg):
        rec["findings"].append({"shape": shape, "msg": msg})

    try:
        tid, added = ensure_title(slug, state, host)
    except RuntimeError as e:
        # adding the FMA to the fleet failed (e.g. #55360's 500): a result in its own right, not a crash
        find("add", str(e))
        rec["outcome"] = "findings"
        return finish(rec)
    rec.update(title_id=tid, title_added_by_harness=added)
    pre = present(host, exists_q)
    rec["present_before"] = pre
    key = f"{slug}@{host}"
    if pre and key not in state["harness_installed"]:
        rec["outcome"] = "skipped: app already on host and not installed by the harness"
        return finish(rec)
    if pre is None:
        rec["outcome"] = "skipped: host didn't answer the presence query"
        return finish(rec)

    if not queue_idle(host):
        rec["outcome"] = f"skipped: the host's queue was still busy after {STEP_TIMEOUT // 60} min; nothing queued"
        return finish(rec)

    for rnd in ("first", "second"):
        # recorded before the request, so a crash mid-cycle never leaves an untracked app on the host
        state["harness_installed"][key] = True; save_state(state, host)
        s = install(host, tid, rec, f"{rnd}_install")
        p = present(host, exists_q)
        status = rec.get(f"{rnd}_install_status")
        if status == "installed" and p is False:
            # per-user installers (Figma) can register the app shortly after they exit; recheck before flagging
            time.sleep(120)
            p = present(host, exists_q)
            rec[f"{rnd}_present_needed_grace"] = p is True
        rec[f"{rnd}_present_after_install"] = p
        if status == "installed" and p is False:
            find("S3", f"{rnd} install: Fleet says installed but the app isn't on the host")
        if status == "failed_install" and p is True:
            find("S3", f"{rnd} install: Fleet says failed but the app is on the host")
        if status not in ("installed",):
            find("install", f"{rnd} install ended as {status}")
        if status == "installed" and rec.get(f"{rnd}_install_error_markers"):
            find("S3", f"{rnd} install: Fleet says installed but the output shows errors: {rec[f'{rnd}_install_error_markers']}")
        if rnd == "first" and status == "installed":
            api("POST", f"/hosts/{host}/refetch")
            inv, t0 = None, time.time()
            while time.time() - t0 < 900 and not inv:
                time.sleep(30)
                sw = host_sw(host, tid) or {}
                inv = [iv.get("version") for iv in sw.get("installed_versions") or []]
            rec["inventory_versions"] = inv
            pkg_ver = ((host_sw(host, tid) or {}).get("software_package") or {}).get("version")
            rec["package_version"] = pkg_ver
            rec["context_after_install"] = context(host, exists_q)
            context_findings(rec["context_after_install"], find, "first install", slug)
            # the patch policy Fleet generates evaluates the manifest's `patched` query; on a host that just got
            # the current version it must return a row (#51377: OneDrive's never could)
            patched_q = v["queries"].get("patched", "")
            if patched_q and pkg_ver and pkg_ver != v["version"]:
                patched_q = patched_q.replace(f"'{v['version']}'", f"'{pkg_ver}'")
            if patched_q:
                rec["patched_query_passes"] = present(host, patched_q)
                if rec["patched_query_passes"] is False:
                    find("S1", f"patched query fails right after installing {pkg_ver}: the patch policy would flag an up-to-date host")
            if not inv:
                find("S1", "inventory never reported the app after install + refetch")
            elif pkg_ver and all(vcmp(x, pkg_ver) < 0 for x in inv):
                if rec.get("patched_query_passes") is True:
                    # the patch query reads another field (e.g. bundle_version) and is fine; only what Fleet shows differs
                    find("S1", f"inventory shows {inv} after installing {pkg_ver}; the patch query passes, so only the "
                               f"version Fleet displays is behind")
                else:
                    find("S1", f"inventory {inv} is lower than the installed package {pkg_ver} (#51377 shape: the patch policy can't pass)")
        uninstall(host, tid, rec, f"{rnd}_uninstall")
        p = present(host, exists_q)
        rec[f"{rnd}_present_after_uninstall"] = p
        ustatus = rec.get(f"{rnd}_uninstall_status")
        if p is True:
            find("S3" if ustatus not in ("failed_uninstall",) else "uninstall", f"{rnd} uninstall: app still on host (status {ustatus})")
        else:
            state["harness_installed"].pop(key, None); save_state(state, host)
    unknown = [k for k, v in rec.items() if k.endswith(("_after_install", "_after_uninstall")) and v is None]
    if unknown:
        find("harness", f"presence unknown after {', '.join(unknown)} (host didn't answer the live query)")
    rec["presence_query_errors"] = PRESENCE_ERRORS[:]
    rec["outcome"] = "pass" if not rec["findings"] else "findings"
    known = CFG.get("known_environment", {}).get(f"{slug}@{host}")
    if known and rec["findings"]:
        rec["outcome"], rec["known_environment"] = "environment (known)", known
    return finish(rec)


def context(host, exists_q, tries=4):
    """Task 3.6 context: where the app landed and with what ownership. Uses the app's own exists query with
    different columns, so it finds exactly what Fleet's detection finds. Returns a dict, or None if unknown."""
    q = exists_q.strip()
    if q.startswith("SELECT 1 FROM apps"):
        q = q.replace("SELECT 1 FROM apps", "SELECT path, bundle_executable, bundle_short_version FROM apps", 1)
        for i in range(tries):
            st, d = api("POST", f"/hosts/{host}/query", {"query": q}, timeout=180)
            rows = [r for r in d.get("rows") or [] if r.get("path", "").startswith("/Applications")] or d.get("rows") or []
            if st == 200 and not d.get("error") and rows:
                app = rows[0]["path"]
                exe = f"{app}/Contents/MacOS/{rows[0]['bundle_executable']}"
                st2, d2 = api("POST", f"/hosts/{host}/query",
                              {"query": f"SELECT path, uid, gid, mode FROM file WHERE path IN ('{app}', '{exe}');"}, timeout=180)
                files = {r["path"]: f"{r['uid']}:{r['gid']} {r['mode']}" for r in d2.get("rows") or []}
                return {"path": app, "bundle": files.get(app), "executable": files.get(exe),
                        "versions": sorted({r.get("bundle_short_version") or "" for r in rows})}
            time.sleep(20)
        return None
    if q.startswith("SELECT 1 FROM programs"):
        q = q.replace("SELECT 1 FROM programs", "SELECT name, version, install_location FROM programs", 1)
        for i in range(tries):
            st, d = api("POST", f"/hosts/{host}/query", {"query": q}, timeout=180)
            if st == 200 and not d.get("error") and d.get("rows"):
                return {"install_locations": sorted({r.get("install_location") or "" for r in d["rows"]}),
                        "versions": sorted({r.get("version") or "" for r in d["rows"]})}
            time.sleep(20)
    return None


def context_findings(ctx, find, label, slug=None):
    if not ctx:
        return
    # per-app expectations (config perm_expectations), e.g. Firefox's updater needs root:admin + group-writable
    # (#55093: Fleet-installed Firefox ended up root:admin rwxr-xr-x and looped on its helper-tool prompt)
    want = CFG.get("perm_expectations", {}).get(slug or "")
    if want and ctx.get("executable"):
        owner, mode = ctx["executable"].split()
        if want.get("owner") and owner != want["owner"]:
            find("S2", f"{label}: executable owner {owner}, expected {want['owner']} ({want.get('why', '')})")
        need = int(want.get("mode_bits", "0"), 8)
        if need and int(mode, 8) & need != need:
            find("S2", f"{label}: executable mode {mode}, needs at least {want['mode_bits']} ({want.get('why', '')})")
    for loc in ctx.get("install_locations", []):
        if "systemprofile" in loc.lower():
            find("S2", f"{label}: installed into the SYSTEM profile ({loc}), not the signed-in user's (#50857)")
    exe = ctx.get("executable")
    if exe:
        mode = int(exe.split()[-1], 8)
        if not mode & 0o001:
            find("S2", f"{label}: app executable isn't runnable by all users ({exe})")


def pin(tid, version):
    """Versions action: pin the title to a cached version ("" = Latest). Multipart, like the UI."""
    import uuid as _uuid
    b = _uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="fleet_id"\r\n\r\n{FID}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="version"\r\n\r\n{version}\r\n--{b}--\r\n').encode()
    req = urllib.request.Request(f"{BASE}/software/titles/{tid}/package", method="PATCH", data=body,
                                 headers={"Authorization": "Bearer " + TOKEN, "User-Agent": "fleet-qa-fma-soak",
                                          "Content-Type": "multipart/form-data; boundary=" + b})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def title_pkg(tid):
    return api("GET", f"/software/titles/{tid}?fleet_id={FID}")[1].get("software_title", {}).get("software_package") or {}


def settle(host, h, was_offline):
    """A VM that just (re)started comes back in stages: boot, auto-login, fleetd and osquery catching up, then whatever
    was queued while it was down. Don't start new work until it has been up loop.settle_minutes, by its own uptime.
    That takes a live query (slow while the host is busy), so only ask after this wait saw the host offline or when
    Fleet reports a restart in the last 70 min (last_restarted_at moves on the hourly detail refresh). If the live
    query fails, don't wait."""
    need = CFG.get("loop", {}).get("settle_minutes", 10) * 60
    r = utc(h.get("last_restarted_at"))
    recent = r and (datetime.datetime.now(datetime.timezone.utc) - r).total_seconds() < need + 3600
    if not (was_offline or recent):
        return
    st, d = api("POST", f"/hosts/{host}/query", {"query": "SELECT total_seconds FROM uptime;"}, timeout=120)
    rows = d.get("rows") or [] if st == 200 else []
    up = int(rows[0]["total_seconds"]) if rows and str(rows[0].get("total_seconds", "")).isdigit() else None
    if up is not None and up < need:
        print(f"host {host} has been up {up // 60} min: waiting {(need - up) // 60 + 1} min for it to settle")
        time.sleep(need - up)


def wait_online(host, minutes=None):
    """True once Fleet shows the host online, waiting up to loop.online_wait_minutes. Work queued for an offline host
    runs whenever it comes back, outside any cycle's tracking, so passes and samples check this first."""
    minutes = CFG.get("loop", {}).get("online_wait_minutes", 30) if minutes is None else minutes
    end = time.time() + minutes * 60
    was_offline = False
    while True:
        st, d = api("GET", f"/hosts/{host}")
        h = d.get("host") or {}
        if h.get("status") == "online":
            settle(host, h, was_offline)
            return True
        was_offline = True
        if time.time() >= end:
            print(f"host {host} is {h.get('status') or st} (last seen {h.get('seen_time')}) after waiting {minutes} min")
            return False
        time.sleep(60)


def cleanup_leftovers(host):
    """Uninstall apps still marked harness-installed, left by an interrupted cycle (restart, crash, sleep, a stopped
    sample). Fleet's own status counts as well as the catalog's exists query, which never matches for #56971-class
    apps. Watcher residents are kept. A title the harness added is removed once its app is gone."""
    import sample as S
    state = load_state(host)
    residents = {x for xs in CFG.get("watcher", {}).get("residents", {}).values() for x in xs}
    out = []
    for key in list(state["harness_installed"]):
        slug = key.rsplit("@", 1)[0]
        if slug in residents:
            continue
        rec = {"slug": slug, "host": host}
        try:
            tid, _ = ensure_title(slug, state, host)
        except RuntimeError as e:
            out.append((slug, f"kept: title unavailable ({e})"))
            continue
        exists_q = catalog_manifest(slug)["versions"][0]["queries"]["exists"]
        sw = host_sw(host, tid) or {}
        on_host = present(host, exists_q) or bool(sw.get("installed_versions")) or sw.get("status") == "installed"
        if on_host:
            uninstall(host, tid, rec, "cleanup")
            # not installed_versions: inventory only drops the app at the host's next refetch, so right after a
            # successful uninstall (status cleared to None) it still lists it
            sw = host_sw(host, tid) or {}
            on_host = present(host, exists_q) or sw.get("status") == "failed_uninstall"
        if on_host:
            out.append((slug, f"still on the host after uninstall (status {rec.get('cleanup_status')}, exit {rec.get('cleanup_exit_code')})"))
            continue
        state = load_state(host)
        state["harness_installed"].pop(key, None)
        save_state(state, host)
        out.append((slug, "removed; title " + S.remove_if_added(slug, host, {})))
    for slug, what in out:
        print(f"cleanup {slug} on {host}: {what}")
    return out


def update_path(slug, host):
    """Task 3.7: install the previous cached version, then upgrade over it to the newest.
    Needs 2+ cached versions; otherwise recorded as skipped (not passed). Always restores Latest."""
    state = load_state(host)
    v = catalog_manifest(slug)["versions"][0]
    rec = {"slug": slug, "host": host, "build": build(), "kind": "update_path",
           "started": datetime.datetime.now().isoformat(timespec="seconds"), "findings": [], "host_at_start": start_watch(host)}
    find = lambda shape, msg: rec["findings"].append({"shape": shape, "msg": msg})
    try:
        tid, _ = ensure_title(slug, state, host)
    except RuntimeError as e:
        find("add", str(e))
        rec["outcome"] = "findings"
        return finish(rec)
    pkg = title_pkg(tid)
    cached = sorted({x["version"] for x in pkg.get("fleet_maintained_versions") or []},
                    key=lambda s: [int(p) for p in re.findall(r"\d+", s)])
    rec.update(title_id=tid, cached_versions=cached, active_before=pkg.get("version"))
    if len(cached) < 2:
        rec["outcome"] = "skipped: one version"
        return finish(rec)
    newest, older = cached[-1], cached[-2]
    if pkg.get("version") != newest:
        rec["outcome"] = "skipped: title is pinned to an older version by someone else; not touching it"
        return finish(rec)
    exists_q, patched_q = v["queries"]["exists"], v["queries"].get("patched", "").replace(f"'{v['version']}'", f"'{newest}'")
    if present(host, exists_q):
        rec["outcome"] = "skipped: app already on host"
        return finish(rec)
    if not queue_idle(host):
        rec["outcome"] = f"skipped: the host's queue was still busy after {STEP_TIMEOUT // 60} min; nothing queued"
        return finish(rec)
    key = f"{slug}@{host}"
    try:
        rec["pin_older_status"] = pin(tid, older)
        rec["active_after_pin"] = title_pkg(tid).get("version")
        if rec["active_after_pin"] != older:
            find("S4", f"pinning {older} left {rec['active_after_pin']} active")
        state["harness_installed"][key] = True; save_state(state, host)
        install(host, tid, rec, "old_install")
        rec["old_patched_query_passes"] = present(host, patched_q) if patched_q else None
        rec["context_old"] = context(host, exists_q)
        context_findings(rec["context_old"], find, f"install of {older}", slug)
        # the pin is only real if the host ends up with that version (#56873: hashless rows can hold newer bytes)
        got = (rec["context_old"] or {}).get("versions") or []
        if got and not any(vcmp(g, older) == 0 for g in got):
            find("S4", f"pinned {older}, but the host has {got} after installing it (#56873 shape)")
        if rec["old_patched_query_passes"] is True:
            find("S1", f"patched query passes with {older} installed: the patch policy can't see this host is outdated")
    finally:
        rec["pin_latest_status"] = pin(tid, "")
        rec["active_after_unpin"] = title_pkg(tid).get("version")
    if rec["active_after_unpin"] != newest:
        find("S4", f"setting Latest left {rec['active_after_unpin']} active, expected {newest}")
    install(host, tid, rec, "upgrade_install")
    rec["upgrade_patched_query_passes"] = present(host, patched_q) if patched_q else None
    rec["context_upgraded"] = context(host, exists_q)
    context_findings(rec["context_upgraded"], find, f"upgrade to {newest}", slug)
    got = (rec["context_upgraded"] or {}).get("versions") or []
    # only an upgrade that ran can be judged by what's on the host (not_run is reported as an install finding below)
    if got and rec.get("upgrade_install_status") == "installed" and not any(vcmp(g, newest) >= 0 for g in got):
        find("S1", f"upgraded to {newest}, but the host still has {got}")
    co, cu = rec.get("context_old") or {}, rec["context_upgraded"] or {}
    for part in ("bundle", "executable"):
        if co.get(part) and cu.get(part) and co[part] != cu[part]:
            find("S2", f"{part} ownership/mode changed by the upgrade: {co[part]} -> {cu[part]} (#55093 shape)")
    if rec.get("upgrade_install_status") != "installed":
        find("install", f"upgrade over {older} ended as {rec.get('upgrade_install_status')}")
    elif rec["upgrade_patched_query_passes"] is False:
        find("S1", f"after upgrading to {newest} the patched query still fails")
    uninstall(host, tid, rec, "cleanup_uninstall")
    if present(host, exists_q) is False:
        state["harness_installed"].pop(key, None); save_state(state, host)
    else:
        find("uninstall", "app still on host after cleanup uninstall")
    rec["outcome"] = "pass" if not rec["findings"] else "findings"
    return finish(rec)


SHOT = os.path.join(HERE, "tools", "shot.mjs")


def scrub(text):
    """Saved output is meant to be publishable (task 4.9): hide local account names in paths."""
    text = re.sub(r"/Users/(?!Shared/)[^/\s]+", "/Users/<user>", text or "")
    text = re.sub(r"(?i)(C:\\Users\\)(?!Public\\)[^\\\s]+", r"\1<user>", text)
    text = re.sub(r"\buser=[^\s\\'\"]+", "user=<user>", text)   # the #51127 probe's "user=<name>"
    # Windows account names ("HOSTNAME\\user"), which name the soak VM; not built-ins or registry roots
    return re.sub(r"(?<![\\\w])(?!(?:AUTHORITY|BUILTIN|HKLM|HKCU|HKCR|HKU|HKEY_\w+|NT SERVICE|SYSTEM)\\)([A-Z][A-Z0-9-]{2,14})\\\\?"
                  r"([A-Za-z0-9._-]+)(?=['\s.:,)]|$)", r"<host>\\<user>", text)


_UI = {}  # the UI session token, once per process


def ui_session():
    """A UI session for screenshots. CI logs in as the fleet-scoped Observer (FMA_SOAK_UI_EMAIL/PASSWORD) once per
    process; sessions expire, so a fixed token can't be a secret. Locally: FMA_SOAK_UI_TOKEN or a fleetctl context."""
    if "token" not in _UI:
        email, pw = os.environ.get("FMA_SOAK_UI_EMAIL"), os.environ.get("FMA_SOAK_UI_PASSWORD")
        if email and pw:
            st, d = api("POST", "/login", {"email": email, "password": pw}, tries=2)
            if st != 200 or not d.get("token"):
                raise RuntimeError(f"UI login failed: {st} {d.get('message')}")
            _UI["token"] = d["token"]
        else:
            _UI["token"] = os.environ.get("FMA_SOAK_UI_TOKEN") or ctx_token(CFG["evidence_context"])
    return _UI["token"]


def evidence(rec, base):
    """Task 3.8: screenshots of the host's Library row and Inventory for the app, taken as a real UI user
    (the harness user is API-only, so Fleet's UI refuses it). Best effort: an expired session leaves a note
    instead, and the record's output tails and inventory still carry the API evidence."""
    shots = {}
    try:
        ui_token = ui_session()
    except Exception as e:
        return {"error": f"no UI session for evidence: {type(e).__name__}: {e}"}
    env = {**os.environ, "FLEET_URL": CFG["instance"], "FLEET_UI_TOKEN": ui_token}
    st, d = api("GET", f"/software/titles/{rec.get('title_id')}?fleet_id={FID}")
    name = (d.get("software_title") or {}).get("name") or rec["slug"].split("/")[0]
    for tab in ("library", "inventory"):
        out = f"{base}-{tab}.png"
        r = subprocess.run(["node", SHOT, f"/hosts/{rec['host']}/software/{tab}?fleet_id={FID}", out, name],
                           capture_output=True, text=True, timeout=180, env=env)
        shots[tab] = os.path.relpath(out, WS) if r.returncode == 0 and os.path.exists(out) else \
            f"unavailable: {(r.stderr or r.stdout).strip()[-120:]}"
    return shots


def finish(rec):
    rec["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    if rec.get("host_at_start"):
        rec["host_at_end"] = host_snapshot(rec["host"])
        why = disruption(rec)
        if why:
            rec["host_disruption"] = why
            # findings from a cycle the host dropped out of aren't evidence; keep them in the record, not the digest
            if rec["findings"] and not str(rec.get("outcome")).startswith("environment"):
                rec["outcome"] = "environment: host restarted or went offline during the cycle (" + "; ".join(why) + ")"
    d = os.path.join(WS, "results", "soak", rec["started"][:10])
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{rec['slug'].replace('/', '_')}-{rec['host']}-{rec['started'][11:19].replace(':', '')}.json")
    if rec["findings"] and (CFG.get("evidence_context") or os.environ.get("FMA_SOAK_UI_EMAIL") or os.environ.get("FMA_SOAK_UI_TOKEN")):
        os.makedirs(os.path.join(d, "evidence"), exist_ok=True)
        try:
            rec["evidence"] = evidence(rec, os.path.join(d, "evidence", os.path.basename(p)[:-5]))
        except Exception as e:
            rec["evidence"] = {"error": f"{type(e).__name__}: {e}"}
    rec["record"] = p
    json.dump(rec, open(p, "w"), indent=1)
    print(f"{rec['slug']} on {rec['host']}: {rec['outcome']}" + "".join(f"\n   [{f['shape']}] {f['msg']}" for f in rec["findings"]))
    print(f"   record: {p}")
    return rec


def run(fn, slug, host):
    """Task 3.8: a cycle with findings runs once more before it counts. Same result twice = reproducible;
    a clean second run = flaky, reported as its own class, never folded into pass."""
    first = fn(slug, host)
    if first.get("outcome") != "findings":
        return first
    print(f"{slug} on {host}: retrying once to check the findings reproduce")
    second = fn(slug, host)
    second["retry_of"] = first["record"]
    if second.get("outcome") == "pass":
        second["outcome"] = "flaky: findings on the first run, clean on the retry"
    elif second.get("outcome") == "findings":
        same = {f["shape"] for f in first["findings"]} & {f["shape"] for f in second["findings"]}
        second["outcome"] = "findings (reproduced on retry)" if same else "findings (different on retry)"
    json.dump(second, open(second["record"], "w"), indent=1)
    print(f"   verdict: {second['outcome']}")
    return second


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--host", type=int)
    ap.add_argument("--canaries", nargs="?", const="all")
    ap.add_argument("--update", action="store_true", help="run the update path (3.7) instead of the lifecycle cycle")
    ap.add_argument("--update-all", choices=["darwin", "windows"], help="update path for every FMA title in the soak fleet with 2+ cached versions")
    ap.add_argument("--cleanup", choices=["darwin", "windows"], help="uninstall apps an interrupted cycle left on the host")
    ap.add_argument("--online", choices=["darwin", "windows"], help="exit 0 once the platform's host is online, 3 if it stays offline")
    a = ap.parse_args()
    if a.online:
        sys.exit(0 if wait_online(CFG["hosts"][a.online][0]) else 3)
    if a.cleanup:
        cleanup_leftovers(CFG["hosts"][a.cleanup][0])
        return
    if a.update_all:
        st, d = api("GET", f"/software/fleet_maintained_apps?fleet_id={FID}&per_page=2000")
        # only titles that already have 2+ cached versions in the soak fleet; the catalog itself publishes one version
        # per app, so older versions exist only where Fleet cached them as upstream moved on
        slugs = []
        for x in d.get("fleet_maintained_apps", []):
            if x.get("software_title_id") and x["slug"].endswith("/" + a.update_all):
                if len((title_pkg(x["software_title_id"]).get("fleet_maintained_versions") or [])) >= 2:
                    slugs.append(x["slug"])
        slugs.sort()
        print(f"{len(slugs)} titles with 2+ cached versions: {slugs}")
        known = CFG.get("known_environment", {})
        for slug in slugs:
            if f"{slug}@{CFG['hosts'][a.update_all][0]}" in known:
                print(f"{slug}: skipped (known environment)"); continue
            try:
                run(update_path, slug, CFG["hosts"][a.update_all][0])
            except Exception as e:
                print(f"{slug}: ERROR {type(e).__name__}: {e}")
        return
    if a.canaries:
        plats = ["darwin", "windows"] if a.canaries == "all" else [a.canaries]
        for p in plats:
            for slug in CFG["canaries"][p]:
                try:
                    run(cycle, slug, CFG["hosts"][p][0])
                except Exception as e:
                    print(f"{slug}: ERROR {type(e).__name__}: {e}")
    elif a.slug:
        plat = a.slug.split("/")[1]
        run(update_path if a.update else cycle, a.slug, a.host or CFG["hosts"][plat][0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
