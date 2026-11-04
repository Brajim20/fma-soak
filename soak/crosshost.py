"""Cross-host repro: a finding on one host reruns on the platform's other host before anyone looks at it.

Untriaged lifecycle and update-path findings (patch-when-closed runs over several passes on the primary) from the last `crosshost.window_days` days that the other host hasn't run since get the same cycle
(lifecycle or update path) on this host, newest first, at most `crosshost.max_per_pass` a pass (`crosshost.priority` slugs first). The new record carries
`crosshost_of` (the finding's record), and the digest and triage.py read the pair:

  both hosts have findings   -> likely a real bug, and it reproduces on two hosts (the bug template's checkbox)
  only the first host        -> look at what differs: the 4 GB VM, Windows on ARM vs x64, what else was installed

A pool runner's findings (pool_slot) rerun on the real hosts too: that's the check on a long-standing VM before
anyone files them. Runs on the host it's given, inside that host's own pass, so CI and manual runs never share a host.

  python3 crosshost.py --platform windows --host 635 [--max 3] [--dry-run]
"""
import argparse, datetime, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as D
import runner as R
import sample as S


def candidates(plat, host):
    cfg = R.CFG.get("crosshost", {})
    since = (datetime.datetime.now() - datetime.timedelta(days=cfg.get("window_days", 14))).isoformat()
    recs = D.all_records()
    # the newest run of each (slug, kind) on this host: a finding older than that has been checked here already
    last_here = {}
    for r in recs:
        if r["host"] == host:
            last_here[(r["slug"], r.get("kind", "lifecycle"))] = r["started"]
    skip = S.excluded(plat, host) - set(R.CFG["canaries"][plat]) | R.busy_slugs()
    out, seen = [], set()
    for r in reversed(recs):
        k = (r["slug"], r.get("kind", "lifecycle"))
        if (k[1] not in ("lifecycle", "update_path") or r["started"] < since or r["host"] == host or (r["host"] not in S.hosts_for(plat) and not r.get("pool_slot")) or k in seen
                or r.get("crosshost_of") or D.category(r) != "findings" or not r["slug"].endswith("/" + plat)):
            continue
        app = f"{r['slug']}@{r['host']}"
        real = [f for f in r["findings"] if f["shape"] != "harness"
                and not D.triage_match(app, f, r) and not D.triage_match(r["slug"], f, r)]
        if not real or last_here.get(k, "") > r["started"] or r["slug"] in skip:
            continue
        seen.add(k)
        out.append(r)
    # crosshost.priority: slugs someone is waiting on go first (the sort is stable, so the rest stay newest first)
    first = set(cfg.get("priority", []))
    return sorted(out, key=lambda r: r["slug"] not in first)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["darwin", "windows"])
    ap.add_argument("--host", type=int)
    ap.add_argument("--max", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    hosts = S.hosts_for(a.platform)
    host = a.host or hosts[0]
    if len(hosts) < 2:
        print(f"crosshost {a.platform}: only one host, nothing to do")
        return
    todo = candidates(a.platform, host)[:a.max or R.CFG.get("crosshost", {}).get("max_per_pass", 3)]
    print(f"crosshost {a.platform} host {host}: {len(todo)} to rerun")
    for r in todo:
        kind = r.get("kind", "lifecycle")
        print(f"   {r['slug']} ({kind}) from host {r['host']} {r['started']}: "
              + "; ".join(f"[{f['shape']}] {f['msg'][:80]}" for f in r["findings"]))
        if a.dry_run or not R.wait_online(host):
            continue
        try:
            rec = R.run(R.update_path if kind == "update_path" else R.cycle, r["slug"], host)
        except Exception as e:
            print(f"   {r['slug']}: ERROR {type(e).__name__}: {e}")
            continue
        rec["crosshost_of"] = D.rel(r["_path"])
        R.json.dump(rec, open(rec["record"], "w"), indent=1)
        if kind != "update_path":
            print(f"   cleanup: {S.remove_if_added(r['slug'], host, rec)}")


if __name__ == "__main__":
    main()
