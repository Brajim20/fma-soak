"""The triage agent's work list (agent/triage-agent.sh): untriaged soak findings, one item per app.

  python3 triage_queue.py --out <dir> --state <state.json> [--max 10]   # <dir>/queue.json, records/, rules.json
  python3 triage_queue.py --assemble <dir>                               # apps/*/ -> decisions.json + DECISIONS.md
  python3 triage_queue.py --record-state <dir> --state <state.json>      # marks the decided apps as done

An item is an app with findings in the last `crosshost.window_days` days that triage.json doesn't cover: the latest
such record per host, the other host's rerun of it (crosshost.py) and each host's latest run of the app, so a later
clean run is visible. Apps already decided are skipped until a newer finding, or the other host's rerun, arrives.
Apps reproduced on both hosts come first, then the newest.

Before the agent sees an item, RULES try to settle it from the records alone (free, no model). An item a rule settles
goes to rules.json, not the queue, and doesn't count against --max. A rule needs evidence in the record, never a guess.
"""
import argparse, datetime, glob, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as D


def untriaged(r):
    app = f"{r['slug']}@{r['host']}"
    return [f for f in r.get("findings", []) if f["shape"] != "harness"
            and not D.triage_match(app, f, r) and not D.triage_match(r["slug"], f, r)]


def rule_open_exit5(slug, by_host, reruns):
    """Windows install over the open app exits 5, and that's the only untriaged finding: the #57149 class."""
    if not slug.endswith("/windows"):
        return None
    for r in by_host.values():
        fs = untriaged(r)
        if not fs or any(not (f["shape"] == "S6" and "install over the open app ended as failed_install" in f["msg"])
                         for f in fs):
            return None
        if "exit code: 5" not in (r.get("open_reinstall_output_tail") or "").lower():
            return None
    return {"decision": "already-filed", "issue": "#57149",
            "summary": "install over the open app exits 5; the install script doesn't close the app first (#57149 class)",
            "hosts": sorted(by_host), "intended": "no: #57149 is open for this class",
            "triage_entry": {"app": slug, "shape": "S6", "msg_contains": "install over the open app ended as failed_install",
                             "verdict": "#57149 class: install over the open app exits 5, the script doesn't close it first"}}


def rule_arm_only(slug, by_host, reruns):
    """Findings only on the Windows on ARM host, and the x64 host's rerun of the same cycle was clean."""
    arm = R_CFG.get("triage_rules", {}).get("windows_arm_host")
    if not slug.endswith("/windows") or set(by_host) != {arm}:
        return None
    clean = [r for r in reruns if r["host"] != arm and D.category(r) == "pass"]
    if not clean:
        return None
    return {"decision": "environment",
            "summary": f"fails only on Windows on ARM ({arm}); the same cycle passed on x64 ({clean[-1]['host']})",
            "hosts": [arm], "intended": "ARM support for x64 FMAs is #42082 / #53050",
            "triage_entry": {"app": f"{slug}@{arm}",
                             "verdict": f"environment: Windows on ARM only; passed on x64 host {clean[-1]['host']} "
                                        f"({D.rel(clean[-1]['_path'])})"}}


RULES = [rule_open_exit5, rule_arm_only]
R_CFG = json.load(open(os.path.join(D.WS, "config.json")))


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
    q, ruled = [], []
    for slug, by_host, reruns, newest in items:
        hit = next((d for d in (rule(slug, by_host, reruns) for rule in RULES) if d), None)
        if hit:
            ruled.append(dict(hit, slug=slug, newest=newest, by="rule"))
            continue
        if len(q) >= n:
            continue
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
    json.dump(ruled, open(os.path.join(out, "rules.json"), "w"), indent=1)
    shutil.copy(os.path.join(D.WS, "triage.json"), os.path.join(out, "triage.json"))
    print(f"{len(items)} undecided apps: {len(ruled)} settled by rules, {len(q)} queued for the agent "
          f"({len(items) - len(ruled) - len(q)} wait for the next run)")
    return q


def safe(slug):
    return slug.replace("/", "-").replace("@", "-")


def assemble(out):
    """apps/<slug>/decision.json (+ BUG_DRAFT_*.md, notes.md) from the per-app sessions, plus rules.json ->
    decisions.json and DECISIONS.md."""
    q = json.load(open(os.path.join(out, "queue.json")))
    ruled = json.load(open(os.path.join(out, "rules.json"))) if os.path.exists(os.path.join(out, "rules.json")) else []
    agent, missing = [], []
    for item in q:
        d = os.path.join(out, "apps", safe(item["slug"]))
        try:
            dec = json.load(open(os.path.join(d, "decision.json")))
        except (OSError, ValueError):
            missing.append(item["slug"])
            continue
        dec = dec[0] if isinstance(dec, list) else dec
        dec.update(slug=item["slug"], by="agent", dir=os.path.relpath(d, out))
        notes = os.path.join(d, "notes.md")
        dec["notes"] = open(notes).read().strip() if os.path.exists(notes) else ""
        drafts = sorted(glob.glob(os.path.join(d, "BUG_DRAFT_*.md")))
        dec["drafts"] = [os.path.relpath(p, out) for p in drafts]
        dec["titles"] = [next((l[6:].strip() for l in open(p) if l.startswith("Title:")), os.path.basename(p)) for p in drafts]
        agent.append(dec)
    alld = ruled + agent
    json.dump(alld, open(os.path.join(out, "decisions.json"), "w"), indent=1)
    bugs = [d for d in agent if d["decision"] == "bug"]
    needs = [d for d in agent if d["decision"] == "needs-you"]
    rest = [d for d in alld if d["decision"] not in ("bug", "needs-you")]
    for d in alld:  # what the review overturned stays visible
        if str(d.get("reviewed", "")).startswith("overturned"):
            d["summary"] = f"{d.get('summary', '')} (review overturned a bug call: {d['reviewed'][12:]})"
    L = [f"# FMA soak triage — {os.path.basename(out)}", "",
         f"{len(alld)} apps decided: **{len(bugs)} draft{'s' if len(bugs) != 1 else ''} ready**, **{len(needs)} need you**, "
         f"{len(rest)} settled ({len(ruled)} by rule, free)." + (f" Not finished, stay queued: {', '.join(missing)}." if missing else ""),
         ""]
    if bugs:
        L += ["## Drafts ready", ""]
        for d in bugs:
            for t, p in zip(d["titles"] or ["(no draft file)"], d["drafts"] or [""]):
                L.append(f"**[{t}]({p})**")
            L += [d.get("summary", ""), f"Reproduced on: {', '.join(map(str, hosts_of(d))) or '—'} · "
                  f"Review: {d.get('reviewed') or 'not reviewed (run ceiling)'}", ""]
            if d["notes"]:
                L += [d["notes"], ""]
    if needs:
        L += ["## Needs you", ""]
        for d in needs:
            L += [f"**{d['slug']}**: {d.get('summary', '')}", ""]
            if d["notes"]:
                L += [d["notes"], ""]
    if rest:
        L += ["## Settled", "", "| App | Decision | Why | Intended? | By |", "|---|---|---|---|---|"]
        for d in rest:
            dec = d["decision"] + (f" {d['issue']}" if d.get("issue") else "")
            L.append(f"| {d['slug']} | {dec} | {d.get('summary', '').replace('|', '/')} | "
                     f"{str(d.get('intended', '')).replace('|', '/')} | {d['by']} |")
        L.append("")
    open(os.path.join(out, "DECISIONS.md"), "w").write("\n".join(L))
    print(f"{len(alld)} decided ({len(ruled)} by rule), {len(bugs)} drafts, {len(needs)} need you, {len(missing)} unfinished")


def hosts_of(d):
    h = d.get("hosts", [])
    return [h] if isinstance(h, (str, int)) else h


def inbox(root):
    """<root>/INBOX.md: every bug draft and needs-you item from all runs that triage.json doesn't cover yet (filing or
    rejecting one adds its entry, which takes it off). Newest decision per app wins."""
    covered = {re.sub(r"@\d+$", "", e["app"]) for e in D.TRIAGE}  # slugs can hold an @ (firefox@developer-edition)
    latest = {}
    for p in sorted(glob.glob(os.path.join(root, "*", "decisions.json"))):
        run = os.path.dirname(p)
        for d in json.load(open(p)):
            if isinstance(d, dict) and d.get("slug"):
                latest[d["slug"]] = (run, d)
    bugs, needs = [], []
    for slug, (run, d) in sorted(latest.items()):
        if slug in covered:
            continue
        if d.get("decision") == "bug":
            bugs.append((run, d))
        elif d.get("decision") == "needs-you":
            needs.append((run, d))
    L = ["# FMA triage inbox", "",
         f"Updated {datetime.datetime.now():%Y-%m-%d %H:%M}. {len(bugs)} draft{'s' if len(bugs) != 1 else ''} to review, "
         f"{len(needs)} need{'s' if len(needs) == 1 else ''} a check. An item leaves this list once triage.json has an "
         "entry for the app (filed, or rejected with a reason).", ""]
    if bugs:
        L += ["## Bug drafts", ""]
        for run, d in bugs:
            drafts = d.get("drafts") or ([os.path.join(d.get("dir", ""), d["draft"])] if d.get("draft") else [])
            titles = d.get("titles") or [d["slug"]]
            for t, p in zip(titles, drafts or [""]):
                L.append(f"- **[{t}]({os.path.join(os.path.basename(run), p)})**")
            L.append(f"  - {d.get('summary', '')} · hosts {', '.join(map(str, hosts_of(d))) or '—'} · "
                     f"review: {d.get('reviewed') or 'not reviewed'} · run {os.path.basename(run)}")
        L.append("")
    if needs:
        L += ["## Needs a check", ""]
        for run, d in needs:
            L.append(f"- **{d['slug']}**: {d.get('summary', '')} ([details]({os.path.basename(run)}/DECISIONS.md))")
        L.append("")
    open(os.path.join(root, "INBOX.md"), "w").write("\n".join(L))
    print(f"inbox: {len(bugs)} drafts, {len(needs)} need a check -> {os.path.join(root, 'INBOX.md')}")
    return len(bugs), len(needs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--state")
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--assemble", metavar="DIR")
    ap.add_argument("--record-state", metavar="DIR")
    ap.add_argument("--inbox", metavar="ROOT")
    a = ap.parse_args()
    if a.inbox:
        inbox(a.inbox)
        return
    if a.assemble:
        assemble(a.assemble)
        return
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    if a.record_state:
        q = {x["slug"]: x["newest"] for x in json.load(open(os.path.join(a.record_state, "queue.json")))}
        q.update({x["slug"]: x["newest"] for x in json.load(open(os.path.join(a.record_state, "rules.json")))}
                 if os.path.exists(os.path.join(a.record_state, "rules.json")) else {})
        p = os.path.join(a.record_state, "decisions.json")
        decided = {d["slug"] for d in (json.load(open(p)) if os.path.exists(p) else []) if d.get("slug") in q}
        state.update({s: q[s] for s in decided})
        json.dump(state, open(a.state, "w"), indent=1)
        print(f"{len(decided)} of {len(q)} apps decided; the rest stay queued")
        return
    days = R_CFG.get("crosshost", {}).get("window_days", 14)
    build(a.out, state, a.max, days)


if __name__ == "__main__":
    main()
