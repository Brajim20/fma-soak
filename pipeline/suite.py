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
  test_pin_holds_on_new_release  pin    a literal pin holds through a new release; Latest then advances
  test_pin_moves_patch_policy    #49474 pinning an older version rewrites the patch policy for it
  test_pinned_script_refresh     #53009 a pinned app still gets script fixes (fails today)
  test_unsatisfiable_caret_rejected  #53008 a caret pin nothing satisfies is rejected (fails today)
GitOps path (fleetctl gitops, each test in its own fleet):
  test_gitops_adds_fma               #42260 an FMA in a fleet file is added at the catalog version
  test_gitops_reapply_no_redownload  #52773 an unchanged re-run downloads and deletes nothing
  test_gitops_dry_run_no_download    #54535 --dry-run downloads nothing and creates nothing
  test_gitops_duplicate_fma_rejected #56507 the same FMA twice in one fleet is an error (fails today)
  test_gitops_caret_unsatisfiable_rejected  #44542 an unsatisfiable caret fails the run cleanly, no panic
  test_gitops_version_error_names_versions  #56508 that error points to Actions > Versions (fails today)
  test_gitops_patch_and_install_policy_same_slug  #43623 #45987 both policies apply; re-run clean
  test_gitops_swap_same_bundle_id    #55140 swapping same-bundle-ID FMAs never leaks "sql: no rows"
  test_generate_gitops_roundtrip     #50694 #41390 #43770 exported fleet file applies back unchanged
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


def pin(tid, fid, version):
    """The Versions action (Actions > Versions): pin a cached version, "^N" for the newest in major N, "" for Latest."""
    return api("PATCH", f"/software/titles/{tid}/package", form={FP: fid, "version": version})


def test_pin_holds_on_new_release(fid):
    """A literal pin keeps the older version active when a new upstream release arrives; Latest then advances."""
    slug = "qa-pin/darwin"
    publish_and_sync(slug, "QA Pin", "1.0.0", "qaapp-1.zip")
    tid = add(slug, fid)[1].get("software_title_id")
    publish_and_sync(slug, "QA Pin", "2.0.0", "qaapp-2.zip")
    run_cron("maintained_apps_auto_update")
    s_pin = pin(tid, fid, "1.0.0")[0]
    pinned = package(tid, fid)
    publish_and_sync(slug, "QA Pin", "3.0.0", "qaapp-3.zip")
    run_cron("maintained_apps_auto_update")
    held = package(tid, fid)
    s_latest = pin(tid, fid, "")[0]
    run_cron("maintained_apps_auto_update")
    latest = package(tid, fid)
    det = {"pin_status": s_pin, "active_after_pin": pinned["version"], "active_after_release": held["version"],
           "pin_after_release": held.get("pinned_version"), "latest_status": s_latest,
           "active_after_latest": latest["version"], "pin_after_latest": latest.get("pinned_version")}
    ok = (s_pin < 300 and pinned["version"] == "1.0.0" and held["version"] == "1.0.0" and held.get("pinned_version") == "1.0.0"
          and s_latest < 300 and latest["version"] == "3.0.0" and not latest.get("pinned_version"))
    return ok, det


def test_pin_moves_patch_policy(fid):
    """#49474: pinning an older version rewrites the patch policy for it (hosts on the pinned version pass);
    Latest moves it back."""
    slug = "qa-pinpolicy/darwin"
    publish_and_sync(slug, "QA Pin Policy", "1.0.0", "qaapp-1.zip", patched=True)
    tid = add(slug, fid)[1].get("software_title_id")
    publish_and_sync(slug, "QA Pin Policy", "2.0.0", "qaapp-2.zip", patched=True)
    run_cron("maintained_apps_auto_update")
    path = "/fleets" if FP == "fleet_id" else "/teams"
    s, d = api("POST", f"{path}/{fid}/policies", {"type": "patch", "patch_software_title_id": tid,
                                                  "software_title_id": tid, "name": "qa-pipe pin policy"})
    pid = (d.get("policy") or {}).get("id")
    if not pid:
        return False, {"error": f"create patch policy {s}: {d.get('message')}"}
    query = lambda: api("GET", f"{path}/{fid}/policies/{pid}")[1]["policy"]["query"]
    q_latest = query()
    pin(tid, fid, "1.0.0")
    q_pinned = query()
    pin(tid, fid, "")
    q_back = query()
    det = {"latest_has_2.0.0": "'2.0.0'" in q_latest, "pinned_has_1.0.0": "'1.0.0'" in q_pinned,
           "pinned_still_has_2.0.0": "'2.0.0'" in q_pinned, "back_has_2.0.0": "'2.0.0'" in q_back}
    ok = det["latest_has_2.0.0"] and det["pinned_has_1.0.0"] and not det["pinned_still_has_2.0.0"] and det["back_has_2.0.0"]
    return ok, det


def test_pinned_script_refresh(fid):
    """#53009: a literal pin gates version advancement, not script fixes; a script-only manifest change for the
    pinned version still reaches the stored script."""
    slug = "qa-pinscript/darwin"
    publish_and_sync(slug, "QA Pin Script", "1.0.0", "qaapp-1.zip", install_script="#!/bin/sh\necho install A\n")
    tid = add(slug, fid)[1].get("software_title_id")
    s_pin = pin(tid, fid, "1.0.0")[0]
    publish_and_sync(slug, "QA Pin Script", "1.0.0", "qaapp-1.zip", install_script="#!/bin/sh\necho install B\n")
    run_cron("maintained_apps_auto_update")
    p = package(tid, fid)
    return "install B" in p["install_script"], {"pin_status": s_pin, "pinned": p.get("pinned_version"),
                                                "install_script": p["install_script"].strip()}


def test_unsatisfiable_caret_rejected(fid):
    """#53008: a caret pin no cached version satisfies is rejected like an unsatisfiable literal pin, and the
    existing pin and active version stay. A satisfiable caret pin is the control."""
    slug = "qa-caret/darwin"
    publish_and_sync(slug, "QA Caret", "1.0.0", "qaapp-1.zip")
    tid = add(slug, fid)[1].get("software_title_id")
    publish_and_sync(slug, "QA Caret", "2.0.0", "qaapp-2.zip")
    run_cron("maintained_apps_auto_update")
    s_ok = pin(tid, fid, "^1")[0]
    control = package(tid, fid)
    s_literal = pin(tid, fid, "9.9.9")[0]
    s_caret = pin(tid, fid, "^9")[0]
    after = package(tid, fid)
    det = {"caret_1_status": s_ok, "active_after_caret_1": control["version"], "literal_9.9.9_status": s_literal,
           "caret_9_status": s_caret, "active_after_caret_9": after["version"], "pin_after_caret_9": after.get("pinned_version")}
    control_ok = s_ok < 300 and control["version"] == "1.0.0" and 400 <= s_literal < 500
    if not control_ok:
        det["error"] = "control failed: a satisfiable caret or an unsatisfiable literal pin didn't behave as expected"
        return False, det
    return 400 <= s_caret < 500 and after["version"] == "1.0.0" and after.get("pinned_version") == "^1", det


# ---------- GitOps path (gap 5): fleetctl gitops against the same fake origin; each test has its own fleet ----------

GITOPS_DIR = os.path.join(ST, "gitops")


def unique_fixture(name):
    """A small zip with bytes no other test uses. GitOps looks installers up by hash across every fleet and fails if
    any one fleet has two active installers with that hash (software_installers.go, "cannot have multiple installers
    with the same hash ... on one team"); the API tests above leave several fixtures sharing qaapp-*.zip in their
    fleet, so GitOps tests get their own bytes."""
    import zipfile
    os.makedirs(os.path.join(ST, "fixtures"), exist_ok=True)
    p = os.path.join(ST, "fixtures", f"{name}.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("payload", os.urandom(2048))
    return p


def gitops(fleet, body, *extra):
    """Apply a fleet file (`name: <fleet>` + body) with fleetctl gitops. Returns (exit code, output)."""
    os.makedirs(GITOPS_DIR, exist_ok=True)
    p = os.path.join(GITOPS_DIR, f"{fleet}.yml")
    open(p, "w").write(f"name: {fleet}\n" + body)
    r = subprocess.run([CTL, "gitops", "--config", CTL_CFG, "-f", p, *extra], capture_output=True, text=True, timeout=900)
    return r.returncode, r.stdout + r.stderr


def fma_yaml(*entries):
    return "software:\n  fleet_maintained_apps:\n" + "".join(
        f"    - slug: {slug}\n" + "".join(f"      {k}: {v}\n" for k, v in extra.items()) for slug, extra in entries)


def fleet_by_name(name):
    path = "/fleets" if FP == "fleet_id" else "/teams"
    d = api("GET", f"{path}?per_page=200")[1]
    return next((t["id"] for t in d.get("fleets", d.get("teams", [])) if t["name"] == name), None)


def title_in(fid, slug):
    rows = api("GET", f"/software/fleet_maintained_apps?{FP}={fid}&per_page=500")[1].get("fleet_maintained_apps", [])
    return next((r.get("software_title_id") for r in rows if r["slug"] == slug), None)


def installer_gets(fname):
    return len([h for h in ORIGIN.hits() if h["file"] == fname and h["method"] == "GET"])


def last_line(out):
    return (out.strip().splitlines() or [""])[-1][:200]


def test_gitops_adds_fma(fid):
    """#42260: an FMA listed in a fleet file is added to that fleet at the catalog version."""
    publish_and_sync("qa-g-add/darwin", "QA G Add", "1.0.0", unique_fixture("qa-g-add"))
    rc, out = gitops("qa-g-add", fma_yaml(("qa-g-add/darwin", {})))
    gf = fleet_by_name("qa-g-add")
    tid = gf and title_in(gf, "qa-g-add/darwin")
    ver = tid and package(tid, gf)["version"]
    return rc == 0 and ver == "1.0.0", {"rc": rc, "fleet": gf, "title": tid, "version": ver, "last": last_line(out)}


def test_gitops_reapply_no_redownload(fid):
    """#52773 (and the #55539 symptom): an unchanged second run downloads nothing and deletes nothing."""
    publish_and_sync("qa-g-re/darwin", "QA G Re", "1.0.0", unique_fixture("qa-g-re"))
    y = fma_yaml(("qa-g-re/darwin", {}))
    rc1, _ = gitops("qa-g-re", y)
    ORIGIN.reset_hits()
    rc2, out = gitops("qa-g-re", y)
    deleted = [l for l in out.splitlines() if re.search(r"delet|remov", l, re.I) and "icons" not in l]
    det = {"rc": [rc1, rc2], "installer_GETs_on_rerun": installer_gets("qa-g-re.zip"), "deleted_lines": deleted}
    return rc1 == rc2 == 0 and det["installer_GETs_on_rerun"] == 0 and not deleted, det


def test_gitops_dry_run_no_download(fid):
    """#54535: --dry-run validates without downloading installers or adding anything."""
    publish_and_sync("qa-g-dry/darwin", "QA G Dry", "1.0.0", unique_fixture("qa-g-dry"))
    ORIGIN.reset_hits()
    rc, out = gitops("qa-g-dry", fma_yaml(("qa-g-dry/darwin", {})), "--dry-run")
    gf = fleet_by_name("qa-g-dry")
    det = {"rc": rc, "installer_GETs": installer_gets("qa-g-dry.zip"), "fleet_created": gf, "last": last_line(out)}
    return rc == 0 and det["installer_GETs"] == 0 and not gf, det


def test_gitops_duplicate_fma_rejected(fid):
    """#56507: the same FMA listed twice in one fleet is an error, not "the last entry's settings silently win"."""
    publish_and_sync("qa-g-dup/darwin", "QA G Dup", "1.0.0", unique_fixture("qa-g-dup"))
    rc, out = gitops("qa-g-dup", fma_yaml(("qa-g-dup/darwin", {"self_service": "true"}), ("qa-g-dup/darwin", {"self_service": "false"})))
    return rc != 0, {"rc": rc, "last": last_line(out)}


def caret_fixture():
    publish_and_sync("qa-g-caret/darwin", "QA G Caret", "1.0.0", unique_fixture("qa-g-caret"))
    return gitops("qa-g-caret", fma_yaml(("qa-g-caret/darwin", {"version": '"^9"'})))


def test_gitops_caret_unsatisfiable_rejected(fid):
    """#44542 (server panic) / #53008: a caret pin no version satisfies fails the run cleanly; the server stays up."""
    rc, out = caret_fixture()
    alive = urllib.request.urlopen("http://localhost:8120/healthz", timeout=10).status == 200
    return rc != 0 and alive and "panic" not in out.lower(), {"rc": rc, "server_alive": alive, "last": last_line(out)}


def test_gitops_version_error_names_versions(fid):
    """#56508: that error tells the admin where versions are listed: Actions > Versions, not Actions > Edit software."""
    rc, out = gitops("qa-g-caret", fma_yaml(("qa-g-caret/darwin", {"version": '"^9"'})))
    return "Actions > Versions" in out, {"rc": rc, "last": last_line(out)}


def test_gitops_patch_and_install_policy_same_slug(fid):
    """#43623 #45987: a patch policy and an install policy for the same FMA both apply, and a re-run is clean."""
    publish_and_sync("qa-g-pol/darwin", "QA G Pol", "1.0.0", unique_fixture("qa-g-pol"), patched=True)
    body = fma_yaml(("qa-g-pol/darwin", {})) + """policies:
  - name: qa-g-pol patch
    type: patch
    fleet_maintained_app_slug: qa-g-pol/darwin
    install_software: true
  - name: qa-g-pol installed
    platform: darwin
    query: "SELECT 1 FROM apps WHERE bundle_identifier = 'com.example.qa-g-pol';"
    install_software:
      fleet_maintained_app_slug: qa-g-pol/darwin
"""
    rc1, out1 = gitops("qa-g-pol", body)
    rc2, out2 = gitops("qa-g-pol", body)
    gf = fleet_by_name("qa-g-pol")
    path = "/fleets" if FP == "fleet_id" else "/teams"
    pols = {p["name"]: p for p in api("GET", f"{path}/{gf}/policies?per_page=100")[1].get("policies") or []} if gf else {}
    det = {"rc": [rc1, rc2], "policies": sorted(pols), "last": last_line(out2 if rc1 == 0 else out1)}
    return rc1 == rc2 == 0 and {"qa-g-pol patch", "qa-g-pol installed"} <= set(pols), det


def test_gitops_swap_same_bundle_id(fid):
    """#55140: replacing an FMA (and its patch policy) with another that has the same bundle identifier either works
    or fails with a clear error; never "sql: no rows in result set". Didn't reproduce with fixtures on 937e874."""
    ORIGIN.publish("qa-g-sa/darwin", "QA G Swap A", "1.0.0", unique_fixture("qa-g-sa"), unique_identifier="com.qa.gswap", patched=True)
    publish_and_sync("qa-g-sb/darwin", "QA G Swap B", "1.0.0", unique_fixture("qa-g-sb"), unique_identifier="com.qa.gswap", patched=True)
    pol = lambda slug: f"policies:\n  - name: qa-g-swap patch\n    type: patch\n    fleet_maintained_app_slug: {slug}\n"
    rc1, _ = gitops("qa-g-swap", fma_yaml(("qa-g-sa/darwin", {})) + pol("qa-g-sa/darwin"))
    rc2, out = gitops("qa-g-swap", fma_yaml(("qa-g-sb/darwin", {})) + pol("qa-g-sb/darwin"))
    leaky = "no rows in result set" in out or "without software title ID" in out
    return rc1 == 0 and not leaky, {"rc": [rc1, rc2], "leaky_error": leaky, "last": last_line(out)}


def test_generate_gitops_roundtrip(fid):
    """#50694 #41390 #43770 #39842: generate-gitops exports a fleet with an FMA as a file that applies back cleanly,
    with the slug (not a hash or an unsupported field) and without changing the title.
    fleetctl reads FMA manifests from Fleet's public catalog itself (its dev override only works with a --dev
    server), so this one uses a real slug served by the local origin with the fixture installer."""
    slug = "google-chrome/darwin"
    publish_and_sync(slug, "Google Chrome", "1.0.0", unique_fixture("qa-g-exp"), unique_identifier="com.google.Chrome")
    rc0, _ = gitops("qa-g-exp", fma_yaml((slug, {"self_service": "true"})))
    gf = fleet_by_name("qa-g-exp")
    tid = gf and title_in(gf, slug)
    out_dir = os.path.join(GITOPS_DIR, "export")
    g = subprocess.run([CTL, "generate-gitops", "--config", CTL_CFG, "--fleet", "qa-g-exp", "--dir", out_dir, "--force",
                        "--insecure"],  # throwaway server: real enroll secrets, else the export has placeholders that don't apply
                       capture_output=True, text=True, timeout=300)
    files = [os.path.join(r, f) for r, _, fs in os.walk(out_dir) for f in fs if f.endswith((".yml", ".yaml"))]
    fleet_file = next((f for f in files if "qa-g-exp" in open(f).read() and re.search(r"^name:", open(f).read(), re.M)), None)
    exported = open(fleet_file).read() if fleet_file else ""
    rc2, out2 = None, ""
    if fleet_file:
        # the export keeps `secrets:` even though enroll secrets are excepted from GitOps by default, so its own
        # output won't apply (#47820, open); drop that block, which isn't what this test is about
        lines, keep, skip = open(fleet_file).read().splitlines(), [], None
        for l in lines:
            ind = len(l) - len(l.lstrip())
            if skip is not None and l.strip() and (ind < skip or (ind == skip and not l.lstrip().startswith("- "))):
                skip = None
            if skip is None and l.strip() == "secrets:":
                skip = ind
                continue
            if skip is None:
                keep.append(l)
        open(fleet_file, "w").write("\n".join(keep) + "\n")
        det_secrets_dropped = len(lines) - len(keep)
        r = subprocess.run([CTL, "gitops", "--config", CTL_CFG, "-f", fleet_file], capture_output=True, text=True,
                           cwd=os.path.dirname(fleet_file), timeout=900)
        rc2, out2 = r.returncode, r.stdout + r.stderr
    tid_after = gf and title_in(gf, slug)
    det = {"generate_rc": g.returncode, "generate_err": (g.stderr or "")[-200:], "fleet_file": fleet_file and os.path.relpath(fleet_file, out_dir),
           "slug_in_export": slug in exported or any(slug in open(f).read() for f in files),
           "reapply_rc": rc2, "reapply_last": last_line(out2 or ""), "title_same": tid == tid_after,
           "secrets_lines_dropped_for_47820": det_secrets_dropped if fleet_file else None}
    return (rc0 == 0 and g.returncode == 0 and det["slug_in_export"] and rc2 == 0 and det["title_same"]), det


# Tests that fail by design until an open bug is fixed. On an RC run they read "known open bug", so a new failure
# stands out; when one starts passing, retest that issue (fleet-bug-retest) and remove it from here.
KNOWN_OPEN = {"test_hash_mismatch_error": "#55360", "test_download_failure_error": "#56891",
              "test_pinned_script_refresh": "#53009", "test_unsatisfiable_caret_rejected": "#53008",
              "test_gitops_duplicate_fma_rejected": "#56507", "test_gitops_version_error_names_versions": "#56508"}

TESTS = [test_newest_version_active, test_script_only_refresh, test_admin_script_edit_kept,
         test_no_redownload_hashless, test_hash_mismatch_error, test_download_failure_error,
         test_patch_policy_follows_promotion, test_pin_holds_on_new_release, test_pin_moves_patch_policy,
         test_pinned_script_refresh, test_unsatisfiable_caret_rejected,
         test_gitops_adds_fma, test_gitops_reapply_no_redownload, test_gitops_dry_run_no_download,
         test_gitops_duplicate_fma_rejected, test_gitops_caret_unsatisfiable_rejected,
         test_gitops_version_error_names_versions, test_gitops_patch_and_install_policy_same_slug,
         test_gitops_swap_same_bundle_id, test_generate_gitops_roundtrip]


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
