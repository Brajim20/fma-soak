"""Task 4.6: the daily digest for #55961 -> results/digest-<date>.md

One file per day, rebuilt from the evidence each time it runs (pass.sh runs it at the end of every pass, so the
day's file fills in as the day goes). What it shows:

- builds tested and loop passes (complete / interrupted / paused)
- what needs a look: findings that reproduced on retry, grouped by failure shape, duplicates collapsed, each with
  its record and evidence; then flaky results, harness problems and known-environment counts
- coverage per platform from the ledger, plus deck progress, and the day's cycle tally
- the L0 audit and Fleet-side confirm for the day, if they ran
- before/after, on a day qa.fleetdm.com's build changed: each app's last result on the old build vs its first
  on the new one

Only the final record of a retried run counts (a record named in another's retry_of is the first attempt).

  python3 digest.py                  # today and yesterday
  python3 digest.py --date 2026-10-29
"""
import argparse, collections, datetime, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
RES = os.path.join(WS, "results")
SHAPES = {"S1": "S1 patch/version detection", "S2": "S2 install context", "S3": "S3 status vs reality",
          "S4": "S4 versions / pinning", "S6": "S6 app open during install/update", "add": "Adding to the fleet failed", "install": "Install didn't complete", "uninstall": "Uninstall didn't complete",
          "harness": "Harness (not Fleet)"}


TRIAGE = json.load(open(os.path.join(WS, "triage.json")))["entries"] if os.path.exists(os.path.join(WS, "triage.json")) else []


def triage_match(app, f, r):
    for t in TRIAGE:
        # "*" covers every soak record (app@host) in an after/before window, e.g. hours the hosts were disrupted;
        # "*@631" only that host's (a host stuck behind one app's hung install)
        wild = t["app"] == "*" and "@" in app or t["app"].startswith("*@") and app.endswith(t["app"][1:])
        if (t["app"] != app and not wild) or ("shape" in t and t["shape"] != f["shape"]):
            continue
        if "msg_contains" in t and t["msg_contains"] not in f["msg"]:
            continue
        if "before" in t and r["started"] >= t["before"]:
            continue
        if "after" in t and r["started"] < t["after"]:
            continue
        return t
    return None


def rel(p):
    return os.path.relpath(p, RES) if p and os.path.isabs(p) else p


def all_records():
    recs = []
    for p in glob.glob(os.path.join(RES, "soak", "*", "*.json")):
        try:
            r = json.load(open(p))
        except (ValueError, OSError):
            continue
        if "slug" in r and "outcome" in r and "started" in r:
            r["_path"] = p
            recs.append(r)
    superseded = {r["retry_of"] for r in recs if r.get("retry_of")}
    return sorted((r for r in recs if r["_path"] not in superseded), key=lambda r: r["started"])


def category(r):
    o = str(r["outcome"])
    if o == "pass":
        return "pass"
    if o.startswith("skipped"):
        return "skipped"
    if o.startswith("environment"):
        return "environment"
    if o.startswith("flaky"):
        return "flaky"
    if o.startswith("error"):
        return "error"
    if all(f["shape"] == "harness" for f in r.get("findings", [])):
        return "harness"
    import stuck
    if stuck.blocked(r):  # waited behind another app's hung install (stuck.py): not evidence about this app
        return "environment"
    return "findings"


def crosshost(r, recs):
    """The other host's rerun of this finding (crosshost.py), as a short phrase, or None if it hasn't run yet."""
    x = [o for o in recs if o.get("crosshost_of") == rel(r["_path"])]
    if not x:
        return None
    c = category(x[-1])
    return (f"reproduced on {x[-1]['host']}" if c == "findings" else f"not on {x[-1]['host']} ({c})") + \
        f" [record]({rel(x[-1]['_path'])})"


def evidence_links(r):
    ev = r.get("evidence") or {}
    links = [f"[record]({rel(r['_path'])})"]
    links += [f"[{k}]({os.path.relpath(os.path.join(WS, v), RES)})" for k, v in ev.items()
              if isinstance(v, str) and v.endswith(".png")]
    return " · ".join(links)


def day_passes(date):
    p = os.path.join(RES, "loop", "passes.jsonl")
    if not os.path.exists(p):
        return []
    return [x for x in map(json.loads, open(p)) if x["started"][:10] == date]


def audit_section(date):
    out = []
    a, c = os.path.join(RES, f"audit-{date}.json"), os.path.join(RES, f"confirm-{date}.json")
    if os.path.exists(a):
        aj = json.load(open(a))
        nonzero = {k: len(v) for k, v in aj["checks"].items() if v}
        out.append(f"Audit of main `{aj.get('sha', '')[:10]}`: " + ", ".join(f"{k} {n}" for k, n in nonzero.items())
                   + f" — [full report](audit-{date}.md)")
    if os.path.exists(c):
        cj = json.load(open(c))
        cnt = collections.Counter(r.get("outcome") for r in cj["results"])
        out.append(f"Fleet-side confirm on {cj['fleet_build']}: " + ", ".join(f"{k} {n}" for k, n in cnt.items())
                   + f" — [full report](confirm-{date}.md)")
        for r in cj["results"]:
            if r.get("error_problems"):
                known = next((t["verdict"] for t in TRIAGE if t["app"] == r["slug"]), None)
                out.append(f"- **{r['slug']}** ({r.get('audit_kind')}): {r.get('status')} — {'; '.join(r['error_problems'])}"
                           + (f" — triaged: {known}" if known else " — **new, needs a look**"))
    n = os.path.join(RES, f"names-{date}.json")
    if os.path.exists(n):
        nj = json.load(open(n))
        bad = [r for r in nj["rows"] if r["verdict"] == "never_matches"]
        out.append(f"Name check (exists query vs the name Windows registers): {nj['counts'].get('ok', 0)} ok, "
                   f"{len(bad)} never match, {nj['counts'].get('suspect', 0)} weak-evidence suspects")
        for r in bad:
            known = next((t["verdict"] for t in TRIAGE if t["app"] == r["slug"]), None)
            out.append(f"- **{r['slug']}** registers {r['names']}" + (f" — triaged: {known}" if known else " — **new, needs a look**"))
        coll = nj.get("collisions") or []
        if coll:
            out.append(f"Windows name collisions (one app's exists query matches another's registered name): {len(coll)}")
            for x in coll:
                known = next((t["verdict"] for t in TRIAGE if t["app"] in (x["query_of"], x["matches_app"])), None)
                out.append(f"- **{x['query_of']}** also matches **{x['matches_app']}** ({x['names'][0]})"
                           + (f" — triaged: {known}" if known else " — **new, needs a look**"))
    if os.path.exists(a):
        for x in json.load(open(a))["checks"].get("bundle_collisions") or []:
            known = next((t["verdict"] for t in TRIAGE if t["app"] in x["slugs"]), None)
            out.append(f"- macOS bundle ID `{x['bundle_identifier']}` is detected by {', '.join(x['slugs'])}"
                       + (f" — triaged: {known}" if known else " — **new, needs a look**"))
    return out or ["No audit ran this day."]


def before_after(recs, date):
    """Only on the day a new build's first records appear."""
    first_seen = {}
    for r in recs:
        first_seen.setdefault(r.get("build"), r["started"][:10])
    new = [b for b, d in first_seen.items() if d == date and b]
    builds = list(first_seen)
    out = []
    for b in new:
        i = builds.index(b)
        if i == 0:
            continue
        old = builds[i - 1]
        last_old, first_new = {}, {}
        for r in recs:
            k = (r["slug"], r["host"], r.get("kind", "lifecycle"))
            if r.get("build") == old:
                last_old[k] = r
            elif r.get("build") == b and k not in first_new:
                first_new[k] = r
        rows = []
        for k in sorted(set(last_old) & set(first_new)):
            a, n = category(last_old[k]), category(first_new[k])
            if a != n:
                verdict = "🔴 regressed" if a == "pass" else ("✅ fixed" if n == "pass" else "changed")
                rows.append(f"| {k[0]}@{k[1]} ({k[2]}) | {a} | {n} | {verdict} |")
        out.append(f"### {old} → {b}")
        out.append(f"{len(set(last_old) & set(first_new))} app runs seen on both builds; {len(rows)} changed.")
        if rows:
            out += ["", "| App | Old build | New build | |", "|---|---|---|---|", *rows]
    return out


def ledger_section():
    p = os.path.join(RES, "ledger.json")
    if not os.path.exists(p):
        return ["No ledger yet."]
    cov = json.load(open(p))["coverage"]
    out = []
    for plat, c in cov.items():
        line = f"- **{plat}**: {c['covered']}/{c['catalog']} apps cycled ({c['percent']}%)"
        decks = c.get("decks") or ({"": c["deck"]} if c.get("deck") else {})
        if len(decks) > 1:  # several hosts: each samples its own share of the catalog
            line += "; " + ", ".join(f"host {h} round {d['round']}: {d['done']}/{d['of']}" for h, d in decks.items())
        elif decks:
            d = next(iter(decks.values()))
            line += f"; deck round {d['round']}: {d['done']}/{d['of']}"
        out.append(line)
        if any("per_day" in d for d in decks.values()):
            out.append("  " + round_pace(decks))
        cp = os.path.join(RES, "sample", f"catalog-{plat}.json")
        if os.path.exists(cp):
            since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
            new = sorted(s for s, d in json.load(open(cp))["first_seen"].items() if d and d >= since)
            if new:
                out.append(f"  new to the catalog in the last 7 days ({len(new)}, first in line): {', '.join(new)}")
    return out


def round_pace(decks):
    """Whether each host's share of the catalog finishes its current round within the target (round_target_days from
    the day that deck's round started). The platform is on pace only if every share is."""
    target = json.load(open(os.path.join(WS, "config.json")))["random_sample"].get("round_target_days", 14)
    parts, late = [], []
    for h, d in decks.items():
        if "per_day" not in d:
            continue
        due = (datetime.date.fromisoformat(d["started"]) + datetime.timedelta(days=target)).isoformat() \
            if d.get("started") else None
        parts.append(f"{h} {d['per_day']}/day → {d.get('eta') or 'never at this speed'}" + (f" (due {due})" if due else ""))
        if due and (not d.get("eta") or d["eta"] > due):
            late.append(str(h))
    if late:
        return f"⚠️ behind the {target}-day round on {', '.join(late)} — " + ", ".join(parts)
    return f"✅ on pace for {target}-day rounds — " + ", ".join(parts)


def digest(date):
    recs = all_records()
    today = [r for r in recs if r["started"][:10] == date]
    passes = day_passes(date)
    if not today and not passes and not os.path.exists(os.path.join(RES, f"audit-{date}.json")):
        return None
    cats = collections.Counter(category(r) for r in today)
    builds = sorted({r.get("build") for r in today if r.get("build")})
    L = [f"# FMA soak digest — {date}", "",
         f"**Build{'s' if len(builds) > 1 else ''}**: {', '.join(builds) or '—'} · "
         f"**Passes**: " + (", ".join(f"{n} {s}" for s, n in collections.Counter(p['status'] for p in passes).items()) or "none recorded"),
         f"**Cycles**: {len(today)} — " + ", ".join(f"{k} {cats[k]}" for k in
                                                   ("pass", "findings", "flaky", "environment", "harness", "skipped", "error") if cats[k]),
         ""]

    import hosts
    import stuck
    avail = hosts.for_date(date) + stuck.for_date(date)
    if avail:  # a quiet day with the hosts down isn't a clean day
        L += ["## Host availability", "", *avail, ""]
    L += ["## Needs a look", ""]
    # (shape) -> (app) -> {msgs, records}; one line per app and shape, however many rounds or runs hit it
    groups = collections.defaultdict(lambda: collections.defaultdict(lambda: {"msgs": [], "recs": []}))
    triaged = collections.defaultdict(lambda: {"n": 0, "rec": None})
    for r in today:
        if category(r) != "findings":
            continue
        app = f"{r['slug']}@{r['host']}"
        for f in r["findings"]:
            if f["shape"] == "harness":
                continue
            t = triage_match(app, f, r)
            if t:
                triaged[(app, t["verdict"])]["n"] += 1
                triaged[(app, t["verdict"])]["rec"] = r
                continue
            g = groups[f["shape"]][app]
            if f["msg"] not in g["msgs"]:
                g["msgs"].append(f["msg"])
            if r not in g["recs"]:
                g["recs"].append(r)
    if not groups:
        L += ["Nothing new today.", ""]
    import context
    needs = []
    for shape in sorted(groups, key=lambda s: list(SHAPES).index(s) if s in SHAPES else 99):
        L += [f"### {SHAPES.get(shape, shape)}", ""]
        for app, g in sorted(groups[shape].items()):
            rs = g["recs"]
            retried = "reproduced on retry" if any("reproduced" in str(r["outcome"]) for r in rs) else "not retried"
            n = f" ({len(rs)} runs)" if len(rs) > 1 else ""
            other = crosshost(rs[-1], recs)
            L.append(f"- **{app}**{n}: {'; '.join(g['msgs'])} ({retried}{'; ' + other if other else ''}) — {evidence_links(rs[-1])}")
            # facts for the "is this intended?" check; not a verdict
            ctx = context.gather(rs[-1]["slug"], shape, rs[-1].get("build"), date)
            L.append(f"  - context: {context.line(ctx)}")
            needs.append({"app": app, "shape": shape, "msgs": g["msgs"], "retried": retried, "runs": len(rs),
                          "other_host": other, "record": os.path.relpath(rs[-1]["_path"], WS), "context": context.line(ctx)})
        L.append("")
    json.dump(needs, open(os.path.join(RES, f"needs-{date}.json"), "w"), indent=1)  # read by notify.py
    if triaged:
        L += ["## Triaged", ""]
        for (app, verdict), t in sorted(triaged.items()):
            L.append(f"- {app}: {verdict} — {evidence_links(t['rec'])}")
        L.append("")

    for cat, title in (("flaky", "Flaky (clean on retry)"), ("harness", "Harness problems"), ("error", "Runner errors")):
        rs = [r for r in today if category(r) == cat]
        if rs:
            L += ["", f"## {title}", ""]
            seen = collections.Counter(f"{r['slug']}@{r['host']}" for r in rs)
            for app, n in seen.items():
                last = [r for r in rs if f"{r['slug']}@{r['host']}" == app][-1]
                msgs = "; ".join(f["msg"] for f in last.get("findings", [])) or last["outcome"]
                L.append(f"- {app}{f' ×{n}' if n > 1 else ''}: {msgs} — {evidence_links(last)}")
    env = collections.Counter(f"{r['slug']}@{r['host']}" for r in today if category(r) == "environment")
    if env:
        L += ["", "## Known environment", "", ", ".join(f"{a} ×{n}" for a, n in env.items())]

    import recheck
    rc = recheck.section()
    if rc:
        L += ["", "## Awaiting QA: soak rechecks (drafts, not posted)", "", *rc]
    L += ["", "## Coverage", "", *ledger_section()]
    ba = before_after(recs, date)
    if ba:
        L += ["", "## Before / after build change", "", *ba]
    L += ["", "## Catalog audit (L0)", "", *audit_section(date), ""]

    path = os.path.join(RES, f"digest-{date}.md")
    tmp = path + ".tmp"
    open(tmp, "w").write("\n".join(L))
    os.replace(tmp, path)  # both platforms' passes call this; never leave a half-written file
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args()
    t = datetime.date.today()
    dates = [a.date] if a.date else [(t - datetime.timedelta(days=1)).isoformat(), t.isoformat()]
    for d in dates:
        p = digest(d)
        print(p or f"{d}: nothing to report")


if __name__ == "__main__":
    main()
