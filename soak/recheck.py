"""QA recheck: when an FMA catalog bug the soak filed reaches Awaiting QA (or is closed), rerun its app and draft the
verification comment.

FMA catalog fixes (manifest, scripts, exists/patched queries) ship within about an hour of merging, not with a
release, so they can be verified on the soak hosts as soon as the fix PR merges. The comment is only a draft: QA
reads it and decides whether to post it. Nothing here posts, moves or closes an issue.

  refresh (report job, needs GH_TOKEN)   the soak's bugs (label recheck.label, ~fma-soak) -> those on the board's
                                         Awaiting QA column (or closed as done; with no board access, any with a
                                         merged fix PR); triage.json's "filed #N" entries give each one's app(s)
                                         (server and fleetd fixes ship with a release: their verdict only counts
                                         once the soak instance runs it) -> results/state/recheck.json,
                                         one item per (issue, app), with the host and kind (lifecycle / update path)
                                         the finding was seen on, and the verdict from any recheck record so far.
                                         Ready-to-paste comments go to results/recheck/<issue>.md. Nothing is posted.
  run (soak pass, per host)              pending items for this host: the same cycle or update path, at least
                                         `recheck.settle_minutes` after the fix PR merged, with `recheck_of` on the
                                         record.

Verdicts, from the newest recheck record:
  fixed           a clean run, with the title freshly added (so Fleet fetched the current manifest)
  still broken    the finding the issue was filed for came back (its triage.json entry matches again)
  different       other findings: look before saying anything on the issue
  inconclusive    clean, but the title was already in the fleet, so the old installer and scripts may have run
  pending         nothing yet, or only skips, environment problems or harness errors (they retry next pass)
Issues that leave Awaiting QA (comment posted and moved on, or sent back) drop out, and so does their draft.

  python3 recheck.py refresh
  python3 recheck.py run --platform windows --host 635 [--max 3] [--dry-run]
  python3 recheck.py show
"""
import argparse, datetime, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as D
import runner as R

QUEUE = os.path.join(D.RES, "state", "recheck.json")
DRAFTS = os.path.join(D.RES, "recheck")


def cfg():
    return R.CFG.get("recheck", {})


def filed():
    """{issue: [triage entries that name it]} for entries tied to one app (not '*' windows)."""
    out = {}
    for t in D.TRIAGE:
        if t["app"].startswith("*"):
            continue
        # the issue the entry is about: the "filed #N" numbers, or else the first one cited ("#57152 class",
        # "missed by #51875" and the like are context, not the bug)
        m = re.search(r"filed\s+((?:#\d{5,6}(?:\s*,\s*|\s+and\s+)?)+)", t["verdict"])
        ns = re.findall(r"#(\d{5,6})", m.group(1)) if m else re.findall(r"#(\d{5,6})", t["verdict"])[:1]
        for n in ns:
            out.setdefault(int(n), []).append(t)
    return out


def fetch_issues(numbers):
    """{n: {title, state, stateReason, closedAt, statuses, prs}} for the issues among `numbers`, in one GraphQL call;
    None if GitHub couldn't be read. `statuses` are the issue's project-board Status values (needs a token that can
    read org projects; GitHub Actions' own token can't, so CI passes FMA_SOAK_GH_PROJECTS_TOKEN). `prs` are merged PRs
    that reference the issue, newest merge last."""
    issue = ('number title state stateReason closedAt '
             'projectItems(first:10){nodes{fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}}} '
             'timelineItems(first:50,itemTypes:[CROSS_REFERENCED_EVENT,CONNECTED_EVENT]){nodes{'
             '... on CrossReferencedEvent{source{... on PullRequest{number merged mergedAt url}}} '
             '... on ConnectedEvent{subject{... on PullRequest{number merged mergedAt url}}}}}')
    q = 'query{repository(owner:"fleetdm",name:"fleet"){' + "".join(
        f"i{n}:issueOrPullRequest(number:{n}){{... on Issue{{{issue}}}}}" for n in sorted(numbers)) + "}}"
    res = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True)
    try:
        repo = json.loads(res.stdout)["data"]["repository"]
    except (ValueError, KeyError, TypeError):
        print(f"recheck: GitHub read failed: {res.stderr.strip()[:300]}")
        return None
    boards = "project" not in res.stderr.lower()
    if not boards:
        print("recheck: this token can't read project boards: treating every open bug with a merged fix PR as waiting on QA")
    out = {}
    for v in repo.values():
        if not v or "number" not in v:
            continue  # a PR, or a number from another repo
        prs = [x.get("source") or x.get("subject") or {} for x in v["timelineItems"]["nodes"]]
        out[v["number"]] = {**{k: v[k] for k in ("number", "title", "state", "stateReason", "closedAt")},
                            "statuses": [(x.get("fieldValueByName") or {}).get("name") or "" for x in v["projectItems"]["nodes"]],
                            "prs": sorted({(p["mergedAt"], p["number"], p["url"]) for p in prs if p.get("merged")}),
                            "boards": boards}
    return out


def mine():
    """Issue numbers of the bugs the soak filed: label recheck.label (fleetdm/fleet)."""
    res = subprocess.run(["gh", "issue", "list", "-R", "fleetdm/fleet", "--label", cfg().get("label", "~fma-soak"),
                          "--state", "all", "--limit", "1000", "--json", "number"],
                         capture_output=True, text=True)
    if res.returncode:
        print(f"recheck: gh issue list failed: {res.stderr.strip()[:300]}")
        return None
    return {i["number"] for i in json.loads(res.stdout)}


def ready(meta):
    """Waiting on QA: open on the board's Awaiting QA column, or closed as done (the owner may close a catalog fix
    directly). Without board access: open with a merged fix PR (wider: it also takes Ready for release and the odd
    Inbox issue whose PR was only related; the drafts are filtered when QA reviews them)."""
    if meta["state"] == "CLOSED":
        return meta.get("stateReason") != "NOT_PLANNED"
    if meta["boards"]:
        return any("awaiting qa" in s.lower() for s in meta["statuses"])
    return bool(meta["prs"])


def origin(entry, recs):
    """(host, kind) of the newest record whose finding this triage entry matched, on a long-standing host."""
    slug = entry["app"].split("@")[0]
    for r in reversed(recs):
        if r["slug"] != slug or r.get("recheck_of") or D.category(r) != "findings":
            continue
        if any(D.triage_match(f"{slug}@{r['host']}", f, r, live=False) is entry or D.triage_match(slug, f, r, live=False) is entry
               for f in r.get("findings", [])):
            return r["host"], r.get("kind") or "lifecycle"
    return None, "lifecycle"


def verdict(item, entries, recs):
    after = item["not_before"]
    mine = [r for r in recs if r.get("recheck_of") == item["issue"] and r["slug"] == item["slug"] and r["started"] >= after]
    real = [r for r in mine if D.category(r) not in ("skipped", "environment", "error", "harness")]
    if not real:
        return "pending", (D.rel(mine[-1]["_path"]) if mine else None)
    r = real[-1]
    app = f"{r['slug']}@{r['host']}"
    if any(D.triage_match(app, f, r, live=False) in entries or D.triage_match(r["slug"], f, r, live=False) in entries for f in r.get("findings", [])):
        return "still broken", D.rel(r["_path"])
    if D.category(r) in ("findings", "flaky"):
        return "different", D.rel(r["_path"])
    if not r.get("title_added_by_harness", True) and (r.get("kind") or "lifecycle") == "lifecycle":
        return "inconclusive", D.rel(r["_path"])
    return "fixed", D.rel(r["_path"])


def refresh():
    by_issue = filed()
    numbers = mine()
    issues = fetch_issues(numbers) if numbers is not None else None
    if issues is None:  # GitHub unreachable: keep the last queue rather than wiping its verdicts
        print("recheck: keeping the previous queue")
        return
    # closed soak bugs for digest.py: a filed entry stops hiding failures that start after its issue closed
    os.makedirs(os.path.dirname(D.ISSUES), exist_ok=True)
    json.dump({n: {"closedAt": m["closedAt"], "stateReason": m["stateReason"]} for n, m in sorted(issues.items())
               if m["state"] == "CLOSED"}, open(D.ISSUES, "w"), indent=1)
    recs = D.all_records()
    old = json.load(open(QUEUE)) if os.path.exists(QUEUE) else {"items": []}
    settle = datetime.timedelta(minutes=cfg().get("settle_minutes", 60))
    now = datetime.datetime.now()
    local = lambda z: datetime.datetime.fromisoformat(z.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    items = []
    for n, meta in sorted(issues.items()):
        if not ready(meta):
            continue
        entries = by_issue.get(n, [])
        if not entries:  # filed outside the soak (or triage.json doesn't say "filed #N"): nothing to rerun
            items.append({"issue": n, "title": meta["title"], "slug": "(no soak app linked)", "platform": None,
                          "host": None, "kind": None, "board": "closed" if meta["state"] == "CLOSED" else
                          ("awaiting QA" if meta["boards"] else "fix merged"), "status": "manual", "record": None,
                          "fix_prs": [{"number": p[1], "url": p[2], "merged": p[0]} for p in meta["prs"]],
                          "not_before": "", "since": "", "seen": ""})
            continue
        for slug in sorted({t["app"].split("@")[0] for t in entries}):
            e = [t for t in entries if t["app"].split("@")[0] == slug]
            prev = next((x for x in old["items"] if x["issue"] == n and x["slug"] == slug), {})
            host, kind = origin(e[0], recs)
            # the fix has to be live before a run counts: settle after the newest merged PR, or after QA first saw
            # the issue waiting when no merged PR is linked
            seen = prev.get("seen") or now.isoformat(timespec="seconds")
            base = local(meta["prs"][-1][0]) if meta["prs"] else datetime.datetime.fromisoformat(seen)
            item = {"issue": n, "title": meta["title"], "slug": slug, "platform": slug.rsplit("/", 1)[1],
                    "host": host, "kind": kind,
                    "board": "closed" if meta["state"] == "CLOSED" else ("awaiting QA" if meta["boards"] else "fix merged"),
                    "seen": seen, "fix_prs": [{"number": p[1], "url": p[2], "merged": p[0]} for p in meta["prs"]],
                    "not_before": (base + settle).isoformat(timespec="seconds")}
            item["status"], item["record"] = verdict(item, e, recs)
            item["since"] = prev.get("since") if prev.get("status") == item["status"] else now.isoformat(timespec="seconds")
            items.append(item)
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    json.dump({"built": now.isoformat(timespec="seconds"), "items": items}, open(QUEUE, "w"), indent=1)
    drafts()
    print(f"recheck: {len(items)} apps on {len({x['issue'] for x in items})} FMA bugs waiting on QA: "
          + ", ".join(f"{s} {sum(1 for x in items if x['status'] == s)}" for s in sorted({x['status'] for x in items})))


def drafts():
    """A comment per issue once every app on it has a verdict worth saying out loud. Never posted from here."""
    q = json.load(open(QUEUE))
    os.makedirs(DRAFTS, exist_ok=True)
    live = {x["issue"] for x in q["items"]}
    for f in os.listdir(DRAFTS):  # an issue that left Awaiting QA (posted and moved on, or sent back) needs no draft
        if f.endswith(".md") and f[:-3].isdigit() and int(f[:-3]) not in live:
            os.remove(os.path.join(DRAFTS, f))
    for n in sorted({x["issue"] for x in q["items"]}):
        xs = [x for x in q["items"] if x["issue"] == n]
        if any(x["status"] in ("pending", "different", "inconclusive", "manual") for x in xs):
            continue
        recs = {x["record"]: json.load(open(os.path.join(D.RES, x["record"]))) for x in xs if x["record"]}
        lines = []
        for x in xs:
            r = recs.get(x["record"], {})
            what = "update path" if x["kind"] == "update_path" else "install → detect → uninstall cycle"
            if x["status"] == "fixed":
                lines.append(f"- ✅ `{x['slug']}`: {what} clean on host {r.get('host')}, {r.get('build')}, "
                             f"catalog version {r.get('catalog_version')} ({r.get('started', '')[:16].replace('T', ' ')})")
            else:
                msgs = "; ".join(f["msg"] for f in r.get("findings", []))[:400]
                lines.append(f"- ❌ `{x['slug']}`: same failure after the close, on host {r.get('host')}, {r.get('build')}, "
                             f"catalog version {r.get('catalog_version')}: {msgs}")
        ok = all(x["status"] == "fixed" for x in xs)
        prs = ", ".join(f"#{p['number']}" for p in xs[0].get("fix_prs", []))
        head = ("QA: verified ✅" if ok else "QA: still reproduces after the fix ❌") + (f" (fix: {prs})" if prs else "")
        body = f"{head}\n\n" + "\n".join(lines) + "\n"
        if not ok:
            body += "\nMoving back for another look.\n"
        open(os.path.join(DRAFTS, f"{n}.md"), "w").write(body)


def run(plat, host, limit, dry):
    if not os.path.exists(QUEUE):
        print("recheck: no queue yet (the report job builds it)")
        return
    hosts = R.CFG["hosts"][plat]
    recs = D.all_records()
    todo = []
    for x in json.load(open(QUEUE))["items"]:
        target = x["host"] if x["host"] in hosts else hosts[0]
        if x["platform"] != plat or x["status"] != "pending" or target != host:
            continue
        if datetime.datetime.now().isoformat() < x["not_before"] or x["slug"] in R.busy_slugs():
            continue
        # one try per pass: a recheck this pass already made (the queue is rebuilt by the report job after it)
        if any(r.get("recheck_of") == x["issue"] and r["slug"] == x["slug"] and r["started"] >= x["not_before"] and D.category(r) not in ("skipped", "environment", "error", "harness")
               for r in recs):
            continue
        todo.append(x)
    todo = todo[:limit]
    print(f"recheck {plat} host {host}: {len(todo)} app(s) waiting on QA to rerun")
    for x in todo:
        print(f"   #{x['issue']} {x['slug']} ({x['kind']}), {x['board']}")
        if dry or not R.wait_online(host):
            continue
        try:
            rec = R.run(R.update_path if x["kind"] == "update_path" else R.cycle, x["slug"], host)
        except Exception as e:
            print(f"   #{x['issue']} {x['slug']}: ERROR {type(e).__name__}: {e}")
            continue
        rec["recheck_of"] = x["issue"]
        json.dump(rec, open(rec["record"], "w"), indent=1)
        print(f"   #{x['issue']} {x['slug']}: {rec.get('outcome')}")
        if x["kind"] != "update_path":
            import sample as S
            print(f"   cleanup: {S.remove_if_added(x['slug'], host, rec)}")


def section():
    """Digest lines: every closed catalog bug and where its recheck stands."""
    if not os.path.exists(QUEUE):
        return []
    items = json.load(open(QUEUE))["items"]
    if not items:
        return ["No FMA catalog bugs waiting on QA."]
    mark = {"fixed": "✅ fixed", "still broken": "❌ still broken", "different": "⚠️ different findings",
            "inconclusive": "⚠️ inconclusive (title was already in the fleet)", "pending": "⏳ pending"}
    out = ["| Issue | App | Board | Fix | Recheck | Record | Draft |", "|---|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda x: (x["status"] != "still broken", x["status"] != "different", -x["issue"])):
        draft = f"[comment](recheck/{x['issue']}.md)" if os.path.exists(os.path.join(DRAFTS, f"{x['issue']}.md")) else ""
        rec = f"[record]({x['record']})" if x.get("record") else ""
        fix = ", ".join(f"[#{p['number']}]({p['url']}) {p['merged'][:10]}" for p in x.get("fix_prs", [])) or "no PR linked"
        out.append(f"| #{x['issue']} | {x['slug']} | {x['board']} | {fix} | {mark.get(x['status'], x['status'])} | {rec} | {draft} |")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["refresh", "run", "show"])
    ap.add_argument("--platform", choices=["darwin", "windows"])
    ap.add_argument("--host", type=int)
    ap.add_argument("--max", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "refresh":
        refresh()
    elif a.cmd == "show":
        print("\n".join(section()))
    else:
        if not a.platform:
            sys.exit("run needs --platform")
        run(a.platform, a.host or R.CFG["hosts"][a.platform][0], a.max or cfg().get("max_per_pass", 5), a.dry_run)


if __name__ == "__main__":
    main()
