"""Email-able alert for new digest findings (task 4.6): an issue in this repo, so the owner hears about them
without opening the digest. Run by the report job after the digest.

One issue per day ("New FMA soak findings <date>"); findings that appear later that day are added as a comment.
results/state/notified.json remembers what was sent, so a finding is announced once, however many times the digest
is rebuilt. Each item carries the digest's context line (facts for the "is this intended?" check, not a verdict).
Nothing here touches fleetdm/fleet: deciding and filing stay with a person.

  python3 notify.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse, datetime, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
RES = os.path.join(WS, "results")
STATE = os.path.join(RES, "state", "notified.json")


def key(n):
    # once per app and failure shape per day; another message for the same pair doesn't email again
    return f"{n['app']}|{n['shape']}"


def body(items, date, repo):
    blob = f"https://github.com/{repo}/blob/results"
    L = [f"New findings in the [digest for {date}]({blob}/results/digest-{date}.md) that need a look:", ""]
    for n in items:
        runs = f", {n['runs']} runs" if n["runs"] > 1 else ""
        L.append(f"- **{n['app']}** ({n['shape']}{runs}, {n['retried']}): {'; '.join(n['msgs'])} — "
                 f"[record]({blob}/{n['record']})")
        L.append(f"  - {n['context']}")
    L += ["", "Check it isn't intended before acting (the context is facts, not a verdict). Then triage per the "
              "README: harness, environment, flaky, catalog (#55961), or Fleet bug (dup-check, then file)."]
    return "\n".join(L)


LIMIT = 60000  # GitHub rejects an issue or comment body over 65536 characters


def parts(text, limit=LIMIT):
    # split on line breaks so each finding stays whole; a single line longer than the limit is cut
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            out.append(line[:limit]); line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    return out + [cur] if cur else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = os.path.join(RES, f"needs-{a.date}.json")
    if not os.path.exists(path):
        print(f"no needs file for {a.date}"); return
    sent = json.load(open(STATE)) if os.path.exists(STATE) else {}
    done = set(sent.get(a.date, []))
    new = [n for n in json.load(open(path)) if key(n) not in done]
    if not new:
        print(f"{a.date}: nothing new to announce"); return
    repo = os.environ.get("GITHUB_REPOSITORY", "Brajim20/fma-soak")
    text = body(new, a.date, repo)
    if a.dry_run:
        print(text); return
    title = f"New FMA soak findings {a.date}"
    found = subprocess.run(["gh", "issue", "list", "-R", repo, "--state", "all", "--search", f'"{title}" in:title',
                            "--json", "number,title", "--jq", f'.[] | select(.title == "{title}") | .number'],
                           capture_output=True, text=True, check=True).stdout.split()
    # the body goes on stdin: a day with hundreds of findings is longer than one command-line argument may be (E2BIG)
    # a day with hundreds of findings is longer than one issue body may be: the rest goes on as comments
    chunks = parts(text)
    if not found:
        url = subprocess.run(["gh", "issue", "create", "-R", repo, "--title", title, "--body-file", "-"],
                             input=chunks.pop(0), text=True, capture_output=True, check=True).stdout.strip()
        found = [url.rsplit("/", 1)[-1]]
    for c in chunks:
        subprocess.run(["gh", "issue", "comment", found[0], "-R", repo, "--body-file", "-"], input=c, text=True, check=True)
    sent[a.date] = sorted(done | {key(n) for n in new})
    for d in sorted(sent)[:-14]:  # keep two weeks
        sent.pop(d)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(sent, open(STATE, "w"), indent=1)
    print(f"{a.date}: announced {len(new)} finding(s)")


if __name__ == "__main__":
    main()
