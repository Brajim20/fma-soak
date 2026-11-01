"""L1 regressions: tests that pass on the latest release and fail on rc or main (the RC gate's L1 half).

Reads each target's latest result (results/state/l1-latest-<target>.json, written by the l1 jobs), writes
results/l1-regressions-<date>.md, prints it for the job summary, and opens/comments the "L1 regression" issue in
this repo when the regression list changes (emails the owner). A test the release fails isn't compared: it's a
feature newer than the release, or a bug customers already have. People close the issue; the bot never does.
"""
import datetime, json, os, subprocess

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(WS, "results")
STATE = os.path.join(RES, "state", "l1-regressions-alerted.json")
TITLE = "L1 regression vs the release"


def latest(target):
    p = os.path.join(RES, "state", f"l1-latest-{target}.json")
    if not os.path.exists(p):
        return None
    meta = json.load(open(p))
    rp = os.path.join(WS, meta["result"])
    return {**meta, "data": json.load(open(rp))} if os.path.exists(rp) else None


def main():
    rel = latest("release")
    lines = [f"# L1 regressions vs the release — {datetime.date.today()}", ""]
    if not rel:
        print("\n".join(lines + ["No release result yet."]))
        return
    passed = {r["test"]: r["passed"] for r in rel["data"]["results"]}
    lines.append(f"Baseline: **{rel['data']['build']}** ({sum(passed.values())}/{len(passed)} pass)")
    found = {}
    for t in ("rc", "main"):
        cur = latest(t)
        if not cur:
            lines.append(f"- {t}: no result")
            continue
        regs = sorted(r["test"] for r in cur["data"]["results"] if passed.get(r["test"]) and not r["passed"])
        found[t] = regs
        lines.append(f"- **{t}** {cur['data']['build']}: " + (", ".join(f"**{x}**" for x in regs) if regs else "no regressions"))
    if any(found.values()):
        lines += ["", "Each one passed on the release: a release blocker until shown otherwise (check the test isn't "
                  "flaky by re-running the target, then find the commit with git bisect between the two revisions)."]
    md = "\n".join(lines) + "\n"
    open(os.path.join(RES, f"l1-regressions-{datetime.date.today()}.md"), "w").write(md)
    print(md)
    seen = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if found == seen.get("last", {}) or not os.environ.get("GH_TOKEN"):
        return
    body = md + f"\nRun: {os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"
    num = subprocess.run(["gh", "issue", "list", "--state", "open", "--search", f'"{TITLE}" in:title', "--json", "number",
                          "--jq", ".[0].number // empty"], capture_output=True, text=True).stdout.strip()
    if num:
        subprocess.run(["gh", "issue", "comment", num, "--body", body], check=True)
    elif any(found.values()):
        subprocess.run(["gh", "issue", "create", "--title", TITLE, "--body", body], check=True)
    seen["last"] = found
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(seen, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main()
