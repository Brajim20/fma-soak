#!/usr/bin/env python3
"""Task 1.10: confirm the audit's suspect apps through Fleet itself (fleetdm/fleet#55961).

For each app the latest audit flagged (dead host, 404/403/web page to curl, unreachable, hash problem),
add it to the soak fleet through the API and record what Fleet does:
  - added fine   -> the URL works for Fleet; the app is removed again right away
  - add failed   -> record status + message, and flag the error if it's a 500 or leaks raw
                    network/Go details (#56891, #55360)

Safety: apps already available in the soak fleet before this run are never touched or removed.

  python3 confirm.py                   uses results/audit-<today>.json
  python3 confirm.py --audit <file>
"""
import argparse, datetime, glob, json, os, re, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import settings
CFG = settings.load()
RESULTS = os.path.join(HERE, "..", "results")
SUSPECT_KINDS = ("url_dead", "url_html_confirm_via_fleet", "url_blocked_needs_manual_check", "url_unreachable",
                 "sha256_mismatch", "download_failed")
# raw error text that shouldn't reach an admin
LEAKY = re.compile(r"lookup \S+ on [\d.:]+|dial tcp|no such host|x509:|context deadline exceeded|"
                   r"connection refused|unexpected EOF|tls:|i/o timeout|stream error|received from peer|INTERNAL_ERROR|\b\d{1,3}(\.\d{1,3}){3}:\d+\b", re.I)


def token():
    import yaml  # PyYAML is on this machine (used elsewhere in the QA workspace)
    if os.environ.get("FMA_SOAK_TOKEN"):  # CI (task 4.9)
        return os.environ["FMA_SOAK_TOKEN"]
    return yaml.safe_load(open(os.path.expanduser("~/.fleet/config")))["contexts"][CFG.get("fleetctl_context", "default")]["token"]


class Fleet:
    def __init__(self):
        self.base = CFG["instance"].rstrip("/") + "/api/latest/fleet"
        self.h = {"Authorization": "Bearer " + token(), "User-Agent": "fleet-qa-fma-soak", "Content-Type": "application/json"}

    def call(self, method, path, body=None, timeout=60):
        req = urllib.request.Request(self.base + path, method=method, headers=self.h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"message": raw[:300].decode(errors="replace")}

    def version(self):
        req = urllib.request.Request(CFG["instance"].rstrip("/") + "/version", headers={"User-Agent": "fleet-qa-fma-soak"})
        with urllib.request.urlopen(req, timeout=30) as r:
            v = json.load(r)
        return f"{v.get('version')} ({v.get('revision', '')[:10]})"

    def fma_row(self, slug, name, fleet_id):
        q = urllib.parse.quote(name[:40])
        st, d = self.call("GET", f"/software/fleet_maintained_apps?fleet_id={fleet_id}&per_page=100&query={q}")
        return next((r for r in d.get("fleet_maintained_apps", []) if r.get("slug") == slug), None)


def latest_audit(path):
    if path:
        return path
    files = sorted(glob.glob(os.path.join(RESULTS, "audit-*.json")))
    if not files:
        raise SystemExit("no audit results yet; run audit.py first")
    return files[-1]


def judge(status, body):
    msg = " ".join([str(body.get("message", ""))] + [str(e.get("reason", "")) for e in body.get("errors", []) or []])
    problems = []
    if status >= 500:
        problems.append("500-level status")
    if LEAKY.search(msg):
        problems.append("raw network/Go error in message")
    return msg.strip(), problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit")
    ap.add_argument("--max", type=int, default=40, help="cap adds per run")
    a = ap.parse_args()
    audit_path = latest_audit(a.audit)
    audit = json.load(open(audit_path))
    # the audit JSON doesn't carry display names; read them from the catalog the audit used
    import audit as A  # same folder
    _, apps, _, _ = A.load_catalog(audit["sha"])
    names = {x["slug"]: x["name"] for x in apps}

    seen, suspects = set(), []
    for items in audit["checks"].values():
        for it in items or []:
            if it.get("kind") in SUSPECT_KINDS and it["slug"] not in seen:
                seen.add(it["slug"])
                suspects.append((it["slug"], it["kind"]))
    suspects = suspects[: a.max]

    fl, fid = Fleet(), CFG["soak_fleet_id"]
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "audit": os.path.basename(audit_path),
           "fleet_build": fl.version(), "fleet_id": fid, "results": []}
    for slug, kind in suspects:
        row = fl.fma_row(slug, names.get(slug, slug.split("/")[0]), fid)
        r = {"slug": slug, "audit_kind": kind}
        if not row:
            r["outcome"] = "not_in_fleet_catalog"
        elif row.get("software_title_id"):
            r["outcome"] = "skipped_already_in_fleet"  # baseline app; never touch
        else:
            st, body = fl.call("POST", "/software/fleet_maintained_apps", {"fleet_maintained_app_id": row["id"], "fleet_id": fid}, timeout=600)
            if st < 300 and body.get("software_title_id"):
                tid = body["software_title_id"]
                dst, _ = fl.call("DELETE", f"/software/titles/{tid}/available_for_install?fleet_id={fid}")
                r.update(outcome="adds_fine", removed=dst in (200, 204), title_id=tid)
            else:
                msg, problems = judge(st, body)
                r.update(outcome="add_failed", status=st, message=msg[:300], error_problems=problems)
        rep["results"].append(r)
        print(f"{slug:45} {r['outcome']:28} {r.get('status', '')} {'; '.join(r.get('error_problems', []))}")

    stamp = rep["run_at"][:10]
    jp, mp = os.path.join(RESULTS, f"confirm-{stamp}.json"), os.path.join(RESULTS, f"confirm-{stamp}.md")
    json.dump(rep, open(jp, "w"), indent=1)
    bad = [x for x in rep["results"] if x.get("error_problems")]
    L = [f"# FMA suspect confirmation — {stamp}", "",
         f"Fleet {rep['fleet_build']} · fleet {fid} · from `{rep['audit']}` · {len(rep['results'])} apps", "",
         "| Outcome | Count |", "|---|---|"]
    for o in ("adds_fine", "add_failed", "skipped_already_in_fleet", "not_in_fleet_catalog"):
        L.append(f"| {o} | {sum(x['outcome'] == o for x in rep['results'])} |")
    L += ["", f"## Bad errors (500 or raw network text): {len(bad)}", ""]
    L += [f"- **{x['slug']}** {x['status']}: `{x['message'][:160]}` — {', '.join(x['error_problems'])}" for x in bad] or ["None."]
    L += ["", "## All results", ""] + [f"- {x['slug']}: {x['outcome']}" + (f" ({x.get('status')}: {x.get('message', '')[:100]})" if x["outcome"] == "add_failed" else "")
                                       for x in rep["results"]]
    open(mp, "w").write("\n".join(L) + "\n")
    print(f"\nreport: {mp}")


if __name__ == "__main__":
    main()
