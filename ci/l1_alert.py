"""Nightly L1 alert: an issue in this repo (emails the owner) when a pipeline result needs a person.

Needs a person: a new failure (verdict FAIL), or a known-open bug's test that now passes (retest that issue).
Only a change emails: results/state/l1-alerted.json remembers what was last reported per target, so a failure that
stays failing is reported once, and its recovery is reported too. People close the issue; the bot never does.

  python3 l1_alert.py <result json> <target>      target: rc or main
"""
import json, os, subprocess, sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(WS, "results", "state", "l1-alerted.json")
TITLE = "L1 pipeline needs a look"


def main():
    path, target = sys.argv[1], sys.argv[2]
    res = json.load(open(path))
    need = sorted(f"{r['test']}: {r['verdict']}" for r in res["results"]
                  if r["verdict"] == "FAIL" or r["verdict"].startswith("PASS (was failing"))
    seen = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if need == seen.get(target, []):
        print(f"{target}: nothing new to report ({len(need)} standing)")
        return
    run = f"{os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"
    lines = [f"**{target}** build {res['build']}:", ""] + [f"- {x}" for x in need or ["all clear again (nothing failing, nothing to retest)"]]
    lines += ["", "FAIL = new failure: check it isn't the harness, then triage like a soak finding. "
              "PASS (was failing …) = that bug looks fixed on this build: retest the issue, then drop the test "
              "from KNOWN_OPEN in pipeline/suite.py.", "", f"Run: {run}"]
    body = "\n".join(lines)
    num = subprocess.run(["gh", "issue", "list", "--state", "open", "--search", f'"{TITLE}" in:title', "--json", "number",
                          "--jq", ".[0].number // empty"], capture_output=True, text=True).stdout.strip()
    if num:
        subprocess.run(["gh", "issue", "comment", num, "--body", body], check=True)
    elif need:
        subprocess.run(["gh", "issue", "create", "--title", TITLE, "--body", body], check=True)
    seen[target] = need
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(seen, open(STATE, "w"), indent=1)
    print(f"{target}: reported {len(need)}")


if __name__ == "__main__":
    main()
