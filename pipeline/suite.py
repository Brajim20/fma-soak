#!/usr/bin/env python3
"""L1 update-pipeline suite (#55961, tasks 2.3-2.6) against the throwaway server from throwaway.sh.

Each test plays the vendor through the local origin: publish a version, add the app, "release" a change,
run the FMA crons, and check what Fleet did. Every test uses its own slug, so they don't interfere.

  python3 suite.py              run all tests, write results/pipeline-<date>.json/.md
  python3 suite.py -k ordering  run tests whose name contains "ordering"

Escapes covered:
  test_newest_version_active     #50968  4-part non-semver versions (151.0.7922.76 -> .109)
  test_script_only_refresh       #51577  a script-only manifest change reaches the stored script
  test_admin_script_edit_kept    #51577  an admin-edited script is not overwritten by the cron
  test_no_redownload_hashless    #53475  unchanged hashless app isn't re-downloaded on every pass
  test_hash_mismatch_error       #55360  hash mismatch on add -> 4xx + plain message (fails today)
  test_download_failure_error    #56891  dead download host on add -> 4xx + plain message (fails today)
  test_patch_policy_follows_promotion  4.3  promotion rewrites the patch policy query + resets its results
"""
import datetime, json, os, re, subprocess, sys, time, urllib.error, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from origin import Origin  # noqa: E402

ST = os.path.join(HERE, "state")
CTL_CFG = os.path.join(ST, "ctl.yml")
BUILD = os.environ.get("PIPE_BUILD", "937e874")
CTL = os.path.join(HERE, "bin", f"fleetctl-{BUILD}")
BASE = "http://localhost:8120/api/latest/fleet"
FIX = os.path.join(HERE, "fixtures")
ORIGIN = Origin(os.path.join(ST, "origin"), 8197)
LEAKY = re.compile(r"lookup \S+ on [\d.:]+|dial tcp|no such host|stream error|x509:|i/o timeout|context deadline", re.I)


def token():
    return re.search(r"token:\s*(\S+)", open(CTL_CFG).read()).group(1)


def api(method, path, body=None, form=None, timeout=600):
    h = {"Authorization": "Bearer " + token()}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if form is not None:
        b = uuid.uuid4().hex
        data = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in form.items()) + f"--{b}--\r\n".encode()
        h["Content-Type"] = "multipart/form-data; boundary=" + b
    req = urllib.request.Request(BASE + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": raw[:200].decode(errors="replace")}


def run_cron(name, timeout=900):
    """Trigger one cron pass and wait for its 'run finished' line (a restart doesn't run the cron)."""
    log = os.path.join(ST, "server.log")
    off = os.path.getsize(log)
    subprocess.run([CTL, "trigger", "--config", CTL_CFG, "--name", name], check=True, capture_output=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        with open(log, "rb") as f:
            f.seek(off)
            new = f.read().decode(errors="replace")
        if re.search(rf'msg="run finished".*cron={name}( |$)|cron={name} .*msg="run finished"', new):
            return new
        time.sleep(1)
    raise TimeoutError(f"cron {name} didn't finish")


FP = "fleet_id"  # query/body param name; "team_id" on builds before the Teams -> Fleets rename


def fleet_id():
    global FP
    # older builds (before the Teams -> Fleets rename) only have /teams and team_id
    new = api("GET", "/fleets?per_page=1")[0] == 200
    path, FP = ("/fleets", "fleet_id") if new else ("/teams", "team_id")
    s, d = api("GET", f"{path}?per_page=100")
    for t in d.get("fleets", d.get("teams", [])):
        if t["name"] == "qa-pipe":
            return t["id"]
    d = api("POST", path, {"name": "qa-pipe"})[1]
    return (d.get("fleet") or d.get("team") or {}).get("id")


def fma_id(slug, fid):
    rows = api("GET", f"/software/fleet_maintained_apps?{FP}={fid}&per_page=500")[1].get("fleet_maintained_apps", [])
    return next(r["id"] for r in rows if r["slug"] == slug)


def add(slug, fid):
    return api("POST", "/software/fleet_maintained_apps", {"fleet_maintained_app_id": fma_id(slug, fid), FP: fid})


def package(title_id, fid):
    return api("GET", f"/software/titles/{title_id}?{FP}={fid}")[1]["software_title"]["software_package"]


def publish_and_sync(slug, name, version, installer, **kw):  # kw: origin.publish options (sha, url, patched, ...)
    ORIGIN.publish(slug, name, version, os.path.join(FIX, installer), **kw)
    run_cron("maintained_apps")


def check_add_error(status, body):
    msg = " ".join([str(body.get("message", ""))] + [str(e.get("reason", "")) for e in body.get("errors", []) or []])
    problems = []
    if status < 400:
        problems.append(f"add unexpectedly succeeded ({status})")
    if status >= 500:
        problems.append(f"status {status}, expected a 4xx")
    if LEAKY.search(msg):
        problems.append("raw network/Go error in the message")
    return msg, problems


# ---------- tests: each returns (passed: bool, details: dict) ----------

def test_newest_version_active(fid):
    slug = "qa-order/darwin"
    publish_and_sync(slug, "QA Order", "151.0.7922.76", "qaapp-1.zip")
    s, d = add(slug, fid)
    tid = d.get("software_title_id")
    publish_and_sync(slug, "QA Order", "151.0.7922.109", "qaapp-2.zip")
    run_cron("maintained_apps_auto_update")
    p = package(tid, fid)
    return p["version"] == "151.0.7922.109", {"active": p["version"], "versions": [v["version"] for v in p.get("fleet_maintained_versions") or []]}


def test_script_only_refresh(fid):
    slug = "qa-script/darwin"
    publish_and_sync(slug, "QA Script", "2.0.0", "qaapp-1.zip", install_script="#!/bin/sh\necho install A\n")
    tid = add(slug, fid)[1].get("software_title_id")
    publish_and_sync(slug, "QA Script", "2.0.0", "qaapp-1.zip", install_script="#!/bin/sh\necho install B\n")
    run_cron("maintained_apps_auto_update")
    got = package(tid, fid)["install_script"]
    return "install B" in got, {"install_script": got.strip()}


def test_admin_script_edit_kept(fid):
    slug = "qa-edited/darwin"
    publish_and_sync(slug, "QA Edited", "3.0.0", "qaapp-1.zip", install_script="#!/bin/sh\necho install A\n")
    tid = add(slug, fid)[1].get("software_title_id")
    s, d = api("PATCH", f"/software/titles/{tid}/package", form={FP: fid, "install_script": "#!/bin/sh\necho admin edit\n"})
    publish_and_sync(slug, "QA Edited", "3.0.0", "qaapp-1.zip", install_script="#!/bin/sh\necho install B\n")
    run_cron("maintained_apps_auto_update")
    got = package(tid, fid)["install_script"]
    return "admin edit" in got, {"edit_status": s, "install_script": got.strip()}


def test_no_redownload_hashless(fid):
    slug = "qa-hashless/darwin"
    publish_and_sync(slug, "QA Hashless", "4.0.0", "qaapp-3.zip", sha="no_check")
    add(slug, fid)
    ORIGIN.reset_hits()
    for _ in range(2):
        run_cron("maintained_apps_auto_update")
    gets = [h for h in ORIGIN.hits() if h["file"] == "qaapp-3.zip" and h["method"] == "GET"]
    return len(gets) == 0, {"installer_GETs_over_2_passes": len(gets)}


def test_hash_mismatch_error(fid):
    slug = "qa-badhash/darwin"
    publish_and_sync(slug, "QA Bad Hash", "5.0.0", "qaapp-2.zip", sha="0" * 64)
    s, d = add(slug, fid)
    msg, problems = check_add_error(s, d)
    return not problems, {"status": s, "message": msg[:200], "problems": problems}


def test_download_failure_error(fid):
    slug = "qa-deadhost/darwin"
    publish_and_sync(slug, "QA Dead Host", "6.0.0", "qaapp-2.zip", url="http://qa-dead-host.invalid/qaapp.zip")
    s, d = add(slug, fid)
    msg, problems = check_add_error(s, d)
    return not problems, {"status": s, "message": msg[:200], "problems": problems}


class FakeHost:
    """A host that exists only as osquery API calls: enrolls into a fleet and reports policy results.
    Enough for Fleet to keep per-host policy membership, which is what a patch policy reset clears."""

    def __init__(self, fid, platform="darwin"):
        secret = "qa-pipe-" + uuid.uuid4().hex[:12]
        path = "/fleets" if FP == "fleet_id" else "/teams"
        s, d = api("PATCH", f"{path}/{fid}/secrets", {"secrets": [{"secret": secret}]})
        if s >= 300:
            raise RuntimeError(f"set enroll secret {s}: {d.get('message')}")
        self.uuid = str(uuid.uuid4()).upper()
        r = self._osq("/api/osquery/enroll", {"enroll_secret": secret, "host_identifier": self.uuid, "host_details": {
            "os_version": {"platform": platform, "name": "macOS", "version": "26.0", "major": "26", "minor": "0"},
            "osquery_info": {"uuid": self.uuid, "version": "5.20.0"},
            "system_info": {"hostname": "qa-pipe-fake", "uuid": self.uuid, "hardware_serial": self.uuid[:12]}}})
        self.node_key = r["node_key"]
        hosts = api("GET", f"/hosts?query={self.uuid[:12]}")[1].get("hosts") or []
        self.id = hosts[0]["id"] if hosts else None

    @staticmethod
    def _osq(path, body):
        req = urllib.request.Request("http://localhost:8120" + path, method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")

    def report_policy(self, pid, passing):
        q = self._osq("/api/osquery/distributed/read", {"node_key": self.node_key}).get("queries", {})
        name = f"fleet_policy_query_{pid}"
        if name not in q:
            return False
        self._osq("/api/osquery/distributed/write", {"node_key": self.node_key, "queries": {name: [{"1": "1"}] if passing else []},
                                                       "statuses": {name: 0}, "messages": {}})
        return True

    def policy_response(self, pid):
        pols = (api("GET", f"/hosts/{self.id}")[1].get("host") or {}).get("policies") or []
        return next((p.get("response") for p in pols if p.get("id") == pid), None)


def test_patch_policy_follows_promotion(fid):
    """What the L3 watcher relies on (4.3): when the auto-update cron promotes a new version, Fleet rewrites the
    patch policy's query for it and resets the policy, so hosts on the old version fail it again and its install
    automation can upgrade them (server/datastore/mysql/software_installers.go SetFleetMaintainedAppActiveInstaller)."""
    slug = "qa-patch/darwin"
    publish_and_sync(slug, "QA Patch", "1.0.0", "qaapp-1.zip", patched=True)
    tid = add(slug, fid)[1].get("software_title_id")
    path = "/fleets" if FP == "fleet_id" else "/teams"
    s, d = api("POST", f"{path}/{fid}/policies", {"type": "patch", "patch_software_title_id": tid,
                                                  "software_title_id": tid, "name": "qa-pipe patch policy"})
    pid = (d.get("policy") or {}).get("id")
    if not pid:
        return False, {"error": f"create patch policy {s}: {d.get('message')}"}
    q1 = api("GET", f"{path}/{fid}/policies/{pid}")[1]["policy"]["query"]
    host = FakeHost(fid)
    reported = host.report_policy(pid, passing=False)
    before = host.policy_response(pid)
    publish_and_sync(slug, "QA Patch", "2.0.0", "qaapp-2.zip", patched=True)
    run_cron("maintained_apps_auto_update")
    pol = api("GET", f"{path}/{fid}/policies/{pid}")[1]["policy"]
    after = host.policy_response(pid)
    det = {"active": package(tid, fid)["version"], "query_v1_has_1.0.0": "'1.0.0'" in q1,
           "query_now_has_2.0.0": "'2.0.0'" in pol["query"], "query_still_has_1.0.0": "'1.0.0'" in pol["query"],
           "install_automation_title": (pol.get("install_software") or {}).get("software_title_id") == tid,
           "host_reported": reported, "host_before": before, "host_after": after}
    ok = (det["active"] == "2.0.0" and det["query_now_has_2.0.0"] and not det["query_still_has_1.0.0"]
          and det["install_automation_title"] and before == "fail" and after in ("", None))
    return ok, det


# Tests that fail by design until an open bug is fixed. On an RC run they read "known open bug", so a new failure
# stands out; when one starts passing, retest that issue (fleet-bug-retest) and remove it from here.
KNOWN_OPEN = {"test_hash_mismatch_error": "#55360", "test_download_failure_error": "#56891"}

TESTS = [test_newest_version_active, test_script_only_refresh, test_admin_script_edit_kept,
         test_no_redownload_hashless, test_hash_mismatch_error, test_download_failure_error,
         test_patch_policy_follows_promotion]


def main():
    k = sys.argv[sys.argv.index("-k") + 1] if "-k" in sys.argv else ""
    ver = json.load(urllib.request.urlopen("http://localhost:8120/version"))
    fid = fleet_id()
    results = []
    for t in TESTS:
        if k and k not in t.__name__:
            continue
        t0 = time.time()
        try:
            ok, det = t(fid)
        except Exception as e:
            ok, det = False, {"error": f"{type(e).__name__}: {e}"}
        known = KNOWN_OPEN.get(t.__name__)
        verdict = ("PASS" if not known else f"PASS (was failing for {known}: retest it)") if ok else \
                  (f"KNOWN {known}" if known else "FAIL")
        results.append({"test": t.__name__, "passed": ok, "verdict": verdict, "seconds": round(time.time() - t0), **det})
        print(f"{verdict:12}  {t.__name__:32} {json.dumps(det)[:180]}")
    stamp = datetime.date.today().isoformat()
    out = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "build": f"{ver['version']} ({ver['revision'][:10]})", "results": results}
    os.makedirs(os.path.join(HERE, "..", "results"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "..", "results", f"pipeline-{stamp}-{BUILD}.json"), "w"), indent=1)
    L = [f"# L1 update-pipeline suite — {stamp}", "", f"Build: {out['build']}", "", "| Test | Result | Details |", "|---|---|---|"]
    icon = lambda r: "✅" if r["passed"] else ("⏳" if r["verdict"].startswith("KNOWN") else "❌")
    L += [f"| {r['test']} | {icon(r)} {r['verdict']} | {json.dumps({x: y for x, y in r.items() if x not in ('test', 'passed', 'verdict', 'seconds')})[:160]} |" for r in results]
    new_fail = [r["test"] for r in results if r["verdict"] == "FAIL"]
    L += ["", f"New failures: {', '.join(new_fail) if new_fail else 'none'}"]
    open(os.path.join(HERE, "..", "results", f"pipeline-{stamp}-{BUILD}.md"), "w").write("\n".join(L) + "\n")
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} passed · new failures: {len(new_fail)} · build {out['build']}")
    sys.exit(1 if new_fail else 0)


if __name__ == "__main__":
    main()
