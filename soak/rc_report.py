"""RC gate, soak half: which apps regressed on the build qa.fleetdm.com runs now, compared with the build before it.

A regression here is an app run (app, host, lifecycle or update path) that passed on the previous build and has
findings on the current one, or that gained a failure shape it didn't have before. Bugs the previous build already
had are left out on purpose: customers have those already, so they can't block a release. That keeps the list short
enough for the release DRI to act on before the release ships.

Builds are told apart by revision (the same revision can carry different version labels). The baseline is the
newest revision before the current one with at least `min_baseline_runs` app runs, so a build that was up for an hour
doesn't become the baseline.

  python3 rc_report.py            # results/rc-report-<rev>.md/.json for the current build, prints the verdict
  python3 rc_report.py --alert    # also opens/comments the "RC regression suspects" issue when the list changes

Canaries and the update path rerun as soon as the build changes (pass.sh), so the canaries are on both builds within
a pass or two of a new RC landing.
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as D

STATE = os.path.join(D.RES, "state", "rc-report-alerted.json")
TITLE = "RC regression suspects"
MIN_BASELINE_RUNS = 20
BAD = ("findings",)          # categories that count as failing; environment/skipped/flaky/harness/error don't


def rev(build):
    m = re.search(r"\(([0-9a-f]{7,40})\)", build or "")
    return m.group(1)[:10] if m else None


def label(build_by_rev, r):
    return f"{build_by_rev[r]}" if r in build_by_rev else r


def report():
    recs = [r for r in D.all_records() if rev(r.get("build"))]
    order, count, name = [], {}, {}
    for r in recs:
        v = rev(r["build"])
        if v not in count:
            order.append(v)
        count[v] = count.get(v, 0) + 1
        name.setdefault(v, r["build"])
    if not order:
        return None
    cur = order[-1]
    base = next((v for v in reversed(order[:-1]) if count[v] >= MIN_BASELINE_RUNS), None)
    rep = {"current": name[cur], "current_rev": cur, "baseline": name.get(base), "baseline_rev": base,
           "current_runs": count[cur], "regressions": [], "fixed": [], "compared": 0, "canaries_not_yet_on_current": []}
    if not base:
        return rep
    last = {base: {}, cur: {}}
    for r in recs:
        v = rev(r["build"])
        if v in last:
            last[v][(r["slug"], r["host"], r.get("kind", "lifecycle"))] = r
    both = sorted(set(last[base]) & set(last[cur]))
    rep["compared"] = len(both)
    for k in both:
        a, b = last[base][k], last[cur][k]
        ca, cb = D.category(a), D.category(b)
        shapes_a = {f["shape"] for f in a.get("findings", [])}
        new_shapes = sorted({f["shape"] for f in b.get("findings", [])} - shapes_a - {"harness"})
        row = {"app": f"{k[0]}@{k[1]}", "kind": k[2], "baseline": ca, "current": cb,
               "baseline_record": D.rel(a["_path"]), "current_record": D.rel(b["_path"]),
               "findings": [f"{f['shape']}: {f['msg']}" for f in b.get("findings", []) if f["shape"] in new_shapes or ca == "pass"],
               "triaged": [t["verdict"] for f in b.get("findings", []) if (t := D.triage_match(f"{k[0]}@{k[1]}", f, b))
                           or (t := D.triage_match(k[0], f, b))]}
        if cb in BAD and (ca == "pass" or (ca in BAD and new_shapes)):
            row["why"] = "passed on the baseline" if ca == "pass" else f"new failure shape {', '.join(new_shapes)}"
            row["reproduced"] = "reproduced on retry" in str(b["outcome"])
            rep["regressions"].append(row)
        elif ca in BAD and cb == "pass":
            rep["fixed"].append(row)
    canaries = {s for xs in json.load(open(os.path.join(D.WS, "config.json")))["canaries"].values() for s in xs} \
        if os.path.exists(os.path.join(D.WS, "config.json")) else set()
    on_cur = {k[0] for k in last[cur]}
    rep["canaries_not_yet_on_current"] = sorted(canaries - on_cur)
    return rep


def write(rep):
    os.makedirs(D.RES, exist_ok=True)
    base = os.path.join(D.RES, f"rc-report-{rep['current_rev']}")
    json.dump(rep, open(base + ".json", "w"), indent=1)
    n = len(rep["regressions"])
    L = [f"# RC regression report: {rep['current']}", "",
         f"Baseline: **{rep['baseline'] or '—'}** · app runs on the current build: {rep['current_runs']} · "
         f"compared on both builds: {rep['compared']}", ""]
    if not rep["baseline"]:
        L.append("No baseline yet: this is the only build with enough runs.")
    else:
        L.append(f"**{'No regression suspects' if not n else f'{n} regression suspect' + ('s' if n > 1 else '')}**"
                 " (passed on the baseline, or a new failure shape, and failing now). Bugs the baseline already had "
                 "aren't listed: customers have those already.")
        if rep["canaries_not_yet_on_current"]:
            L.append(f"\nCanaries not yet run on this build: {', '.join(rep['canaries_not_yet_on_current'])} "
                     "(the verdict isn't complete until they have).")
        if n:
            L += ["", "| App | Path | Why | Retry | Findings now | Triaged as |", "|---|---|---|---|---|---|"]
            for r in rep["regressions"]:
                L.append(f"| {r['app']} | {r['kind']} | {r['why']} | {'reproduced' if r['reproduced'] else '—'} | "
                         f"{'<br>'.join(r['findings'])[:400]} | {'; '.join(r['triaged'])[:200] or '**new**'} |")
            L += ["", "Next: check each against the baseline's code (is the failing step in Fleet's code or the app's "
                  "manifest?). Catalog causes aren't release blockers: the catalog ships on its own."]
        if rep["fixed"]:
            L += ["", f"Fixed on this build ({len(rep['fixed'])}): " + ", ".join(f"{r['app']} ({r['kind']})" for r in rep["fixed"])]
    open(base + ".md", "w").write("\n".join(L) + "\n")
    return base + ".md"


def alert(rep):
    """An issue in this repo (emails the owner) when the suspect list for a build changes; the bot never closes it."""
    seen = json.load(open(STATE)) if os.path.exists(STATE) else {}
    now = sorted(f"{r['app']} ({r['kind']})" for r in rep["regressions"])
    if now == seen.get(rep["current_rev"], []):
        print("rc report: nothing new to report")
        return
    body = "\n".join([f"**{rep['current']}** vs baseline **{rep['baseline']}**:", ""] +
                     [f"- {x}" for x in now or ["no suspects any more"]] +
                     ["", f"Report: results/rc-report-{rep['current_rev']}.md on the `results` branch."])
    num = subprocess.run(["gh", "issue", "list", "--state", "open", "--search", f'"{TITLE}" in:title', "--json", "number",
                          "--jq", ".[0].number // empty"], capture_output=True, text=True).stdout.strip()
    if num:
        subprocess.run(["gh", "issue", "comment", num, "--body", body], check=True)
    elif now:
        subprocess.run(["gh", "issue", "create", "--title", TITLE, "--body", body], check=True)
    seen[rep["current_rev"]] = now
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(seen, open(STATE, "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true")
    a = ap.parse_args()
    rep = report()
    if not rep:
        print("rc report: no records")
        return
    path = write(rep)
    print(f"rc report: {rep['current']} vs {rep['baseline']}: {len(rep['regressions'])} suspects, "
          f"{rep['compared']} compared -> {path}")
    if a.alert and rep["baseline"]:
        alert(rep)


if __name__ == "__main__":
    main()
