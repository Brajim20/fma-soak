"""Task 4.8: draft the monthly #55961 status comment -> results/monthly-<YYYY-MM>.md

Draft only: it is never posted automatically. Pulls, for the month:
- builds the soak ran on, cycles and their results, coverage now (ledger) and deck rounds
- escapes caught: issues referenced in triage.json, with their current state from GitHub
- build changes seen (from the digests' before/after logic)
- close-out criteria with where each stands
"Plan changes" is left for the tester to write.

  python3 monthly.py                 # the current month
  python3 monthly.py --month 2026-10
"""
import argparse, collections, datetime, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as D
sys.path.insert(0, os.path.dirname(HERE))
import settings as S

WS = D.WS


def gh_state(n):
    out = subprocess.run(["gh", "issue", "view", str(n), "-R", "fleetdm/fleet", "--json", "state,title,createdAt"],
                         capture_output=True, text=True).stdout
    try:
        j = json.loads(out)
        return j["state"].lower(), j["title"], j["createdAt"][:7]
    except ValueError:
        return "unknown", "", ""


def criteria(cov):
    """Proposed close-out criteria for #55961; each line says where it stands from the evidence."""
    def full_round(plat):
        d = (cov.get(plat) or {}).get("deck") or {}
        return d.get("round", 1) > 1
    act = os.path.exists(os.path.join(WS, "ci", "ACTIVE"))
    return [
        ("Every failure shape (S1–S6) has an automated check whose teeth were shown on a pre-fix build or script",
         "see the coverage plan's escape matrix"),
        ("The whole catalog cycled at least once per platform",
         ", ".join(f"{p}: {'done' if full_round(p) else 'in progress'}" for p in ("darwin", "windows"))),
        ("One real upstream transition followed end to end (L3)",
         "yes" if any(f.endswith(".json") and "-transition-" in f for _, _, fs in os.walk(os.path.join(WS, "results", "soak")) for f in fs) else "not yet"),
        ("The harness runs on GitHub Actions for a full week", "yes" if act else "not yet"),
        ("Every finding in the month's digests is adjudicated (filed, linked, or triaged)", "check the digests"),
    ]


def monthly(month):
    recs = [r for r in D.all_records() if r["started"][:7] == month]
    cats = collections.Counter(D.category(r) for r in recs)
    builds = sorted({r.get("build") for r in recs if r.get("build")})
    led = json.load(open(os.path.join(WS, "results", "ledger.json"))) if os.path.exists(os.path.join(WS, "results", "ledger.json")) else {"coverage": {}}
    cov = led["coverage"]
    triaged = {int(n) for t in D.TRIAGE for n in re.findall(r"#(\d{5})", t["verdict"])}
    issues = set(triaged)
    # plus FMA bugs from a filed-bugs tracker, if there is one (a markdown table: | #NNNNN | title | ...)
    idx = os.environ.get("FMA_SOAK_FILED_BUGS", "")
    if idx and os.path.exists(idx):
        for line in open(idx):
            m = re.match(r"\| #(\d+) \| ([^|]+)\|", line)
            if m and re.search(r"Fleet-maintained|\bFMA\b|uninstall reports success", m.group(2)):
                issues.add(int(m.group(1)))
    issues = sorted(issues)
    L = [f"## FMA soak — {datetime.date.fromisoformat(month + '-01'):%B %Y} update", "",
         f"**Environment:** {S.load()['instance'].split('//')[-1]}, fleet {S.load()['soak_fleet_id']} · builds: {', '.join(builds) or '—'}", ""]
    L += ["**1. Runs** — " + f"{len(recs)} cycles: " + ", ".join(f"{k} {cats[k]}" for k in
          ("pass", "findings", "flaky", "environment", "harness", "skipped", "error") if cats[k]), ""]
    covl = [f"{p} {c['covered']}/{c['catalog']} ({c['percent']}%)" + (f", round {c['deck']['round']}" if c.get("deck") else "")
            for p, c in cov.items()]
    L += [f"**2. Coverage** — {'; '.join(covl) or '—'}", ""]
    L += ["**3. Escapes caught or confirmed**", ""]
    if issues:
        L += ["| Issue | State | Title |", "|---|---|---|"]
        for n in issues:
            st, title, created = gh_state(n)
            if n in triaged or created == month:  # the soak hit it, or it was filed this month
                L.append(f"| #{n} | {st} | {title} |")
    else:
        L.append("None this month.")
    ba = [l for d in sorted({r["started"][:10] for r in recs}) for l in D.before_after(D.all_records(), d)]
    L += ["", "**4. Build changes**", ""] + (ba or ["No build change this month."])
    L += ["", "**5. Plan changes**", "", "TODO: what changed in the plan this month.", ""]
    L += ["**6. Close-out criteria**", ""]
    for i, (c, where) in enumerate(criteria(cov), 1):
        L.append(f"{i}. {c} — {where}")
    out = os.path.join(WS, "results", f"monthly-{month}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write("\n".join(L) + "\n")
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=datetime.date.today().strftime("%Y-%m"))
    monthly(ap.parse_args().month)


if __name__ == "__main__":
    main()
