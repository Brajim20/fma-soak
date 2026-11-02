"""The triage agent's work list (agent/triage-agent.sh): untriaged soak findings, one item per app.

  python3 triage_queue.py --out <dir> --state <state.json> [--max 10]   # writes <dir>/queue.json + <dir>/records/
  python3 triage_queue.py --record-state <dir> --state <state.json>      # marks the agent's decided apps as done

An item is an app with findings in the last `crosshost.window_days` days that triage.json doesn't cover: the latest
such record per host, the other host's rerun of it (crosshost.py) and each host's latest run of the app, so a later
clean run is visible. Apps the agent already decided are skipped until a newer finding, or the other host's rerun,
arrives. Apps reproduced on
both hosts come first, then the newest.
"""
import argparse, datetime, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as D


def untriaged(r):
    app = f"{r['slug']}@{r['host']}"
    return [f for f in r.get("findings", []) if f["shape"] != "harness"
            and not D.triage_match(app, f, r) and not D.triage_match(r["slug"], f, r)]


def brief(r, out):
    """The record copied next to the queue (the agent never reads the results checkout, which is deleted after)."""
    dst = os.path.join("records", os.path.basename(r["_path"]))
    shutil.copy(r["_path"], os.path.join(out, dst))
    return {"record": dst, "host": r["host"], "kind": r.get("kind", "lifecycle"), "started": r["started"],
            "build": r.get("build"), "outcome": r["outcome"], "category": D.category(r),
            "findings": [f"[{f['shape']}] {f['msg']}" for f in r.get("findings", [])]}


def build(out, state, n, days):
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    recs = D.all_records()
    found, latest = {}, {}
    for r in recs:
        latest.setdefault(r["slug"], {})[r["host"]] = r
        if r["started"] >= since and D.category(r) == "findings" and not r.get("crosshost_of") and untriaged(r):
            found.setdefault(r["slug"], {})[r["host"]] = r
    items = []
    for slug, by_host in found.items():
        paths = {D.rel(r["_path"]) for r in by_host.values()}
        reruns = [r for r in recs if r.get("crosshost_of") in paths]
        # a decided app comes back when a newer finding or the other host's rerun arrives after the decision
        newest = max([r["started"] for r in by_host.values()] + [r["started"] for r in reruns])
        if state.get(slug, "") >= newest:
            continue
        items.append((slug, by_host, reruns, newest))
    items.sort(key=lambda x: (any(D.category(r) == "findings" for r in x[2]), x[3]), reverse=True)
    os.makedirs(os.path.join(out, "records"), exist_ok=True)
    q = []
    for slug, by_host, reruns, newest in items[:n]:
        name, plat = slug.split("/")
        q.append({"slug": slug, "platform": plat, "newest": newest,
                  "findings": [dict(brief(r, out), untriaged=[f"[{f['shape']}] {f['msg']}" for f in untriaged(r)])
                               for r in by_host.values()],
                  "other_host_reruns": [brief(r, out) for r in reruns],
                  "latest_run_per_host": {str(h): {"started": r["started"], "category": D.category(r)}
                                          for h, r in latest[slug].items()},
                  "catalog": {"output": f"ee/maintained-apps/outputs/{name}/{plat}.json",
                              "input": f"ee/maintained-apps/inputs/{'homebrew' if plat == 'darwin' else 'winget'}/{name}.json"}})
    json.dump(q, open(os.path.join(out, "queue.json"), "w"), indent=1)
    shutil.copy(os.path.join(D.WS, "triage.json"), os.path.join(out, "triage.json"))
    print(f"{len(q)} of {len(items)} undecided apps queued -> {os.path.join(out, 'queue.json')}")
    return q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--state", required=True)
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--record-state", metavar="DIR")
    a = ap.parse_args()
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    if a.record_state:
        q = {x["slug"]: x["newest"] for x in json.load(open(os.path.join(a.record_state, "queue.json")))}
        p = os.path.join(a.record_state, "decisions.json")
        decided = [d["slug"] for d in (json.load(open(p)) if os.path.exists(p) else []) if d.get("slug") in q]
        state.update({s: q[s] for s in decided})
        json.dump(state, open(a.state, "w"), indent=1)
        print(f"{len(decided)} of {len(q)} queued apps decided; the rest stay queued")
        return
    days = json.load(open(os.path.join(D.WS, "config.json"))).get("crosshost", {}).get("window_days", 14)
    build(a.out, state, a.max, days)


if __name__ == "__main__":
    main()
