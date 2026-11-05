"""Closed-bug recheck: when an FMA catalog bug the soak filed gets closed, rerun its app and say whether the fix took.

FMA catalog fixes (manifest, scripts, exists/patched queries) ship within about an hour of merging, not with a release,
so the owner may close them without a QA handoff. This is the QA step instead:

  refresh (report job, needs GH_TOKEN)   triage.json entries that say "filed #N" -> the closed ones among them whose
                                         title starts "Fleet-maintained apps:" (catalog bugs; Fleet server and fleetd
                                         bugs ship with a release and get normal QA) -> results/state/recheck.json,
                                         one item per (issue, app), with the host and kind (lifecycle / update path)
                                         the finding was seen on, and the verdict from any recheck record so far.
                                         Ready-to-paste comments go to results/recheck/<issue>.md. Nothing is posted.
  run (soak pass, per host)              pending items for this host: the same cycle or update path, at least
                                         `recheck.settle_minutes` after the close, with `recheck_of` on the record.

Verdicts, from the newest recheck record:
  fixed           a clean run, with the title freshly added (so Fleet fetched the current manifest)
  still broken    the finding the issue was filed for came back (its triage.json entry matches again)
  different       other findings: look before saying anything on the issue
  inconclusive    clean, but the title was already in the fleet, so the old installer and scripts may have run
  pending         nothing yet, or only skips, environment problems or harness errors (they retry next pass)
Reopened issues drop out; closed as not planned are listed but not rerun.

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
PREFIX = "Fleet-maintained apps:"


def cfg():
    return R.CFG.get("recheck", {})


def filed():
    """{issue: [triage entries that name it]} for entries tied to one app (not '*' windows)."""
    out = {}
    for t in D.TRIAGE:
        if t["app"].startswith("*"):
            continue
        for n in re.findall(r"#(\d{5,6})", t["verdict"]):
            out.setdefault(int(n), []).append(t)
    return out


def closed_issues(numbers):
    """Closed catalog bugs among `numbers`: {n: {title, closedAt, stateReason}}, from one search; None if gh failed."""
    q = ["gh", "issue", "list", "-R", "fleetdm/fleet", "--state", "closed", "--limit", "1000",
         "--search", f'"{PREFIX}" in:title', "--json", "number,title,closedAt,stateReason"]
    res = subprocess.run(q, capture_output=True, text=True)
    if res.returncode:
        return None
    return {i["number"]: i for i in json.loads(res.stdout) if i["number"] in numbers and i["title"].startswith(PREFIX)}


def origin(entry, recs):
    """(host, kind) of the newest record whose finding this triage entry matched, on a long-standing host."""
    slug = entry["app"].split("@")[0]
    for r in reversed(recs):
        if r["slug"] != slug or r.get("recheck_of") or D.category(r) != "findings":
            continue
        if any(D.triage_match(f"{slug}@{r['host']}", f, r) is entry or D.triage_match(slug, f, r) is entry
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
    if any(D.triage_match(app, f, r) in entries or D.triage_match(r["slug"], f, r) in entries for f in r.get("findings", [])):
        return "still broken", D.rel(r["_path"])
    if D.category(r) in ("findings", "flaky"):
        return "different", D.rel(r["_path"])
    if not r.get("title_added_by_harness", True) and (r.get("kind") or "lifecycle") == "lifecycle":
        return "inconclusive", D.rel(r["_path"])
    return "fixed", D.rel(r["_path"])


def refresh():
    by_issue = filed()
    issues = closed_issues(set(by_issue))
    if issues is None:  # GitHub unreachable: keep the last queue rather than wiping its verdicts
        print("recheck: gh issue list failed; keeping the previous queue")
        return
    recs = D.all_records()
    old = json.load(open(QUEUE)) if os.path.exists(QUEUE) else {"items": []}
    settle = datetime.timedelta(minutes=cfg().get("settle_minutes", 60))
    items = []
    for n, meta in sorted(issues.items()):
        entries = by_issue[n]
        for slug in sorted({t["app"].split("@")[0] for t in entries}):
            e = [t for t in entries if t["app"].split("@")[0] == slug]
            host, kind = origin(e[0], recs)
            closed = datetime.datetime.fromisoformat(meta["closedAt"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            item = {"issue": n, "title": meta["title"], "slug": slug, "platform": slug.rsplit("/", 1)[1],
                    "host": host, "kind": kind, "closed": closed.isoformat(timespec="seconds"),
                    "not_before": (closed + settle).isoformat(timespec="seconds")}
            if meta.get("stateReason") == "NOT_PLANNED":
                item.update(status="not planned", record=None)
            else:
                item["status"], item["record"] = verdict(item, e, recs)
            prev = next((x for x in old["items"] if x["issue"] == n and x["slug"] == slug), {})
            item["since"] = prev.get("since") if prev.get("status") == item["status"] else datetime.datetime.now().isoformat(timespec="seconds")
            items.append(item)
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    json.dump({"built": datetime.datetime.now().isoformat(timespec="seconds"), "items": items}, open(QUEUE, "w"), indent=1)
    drafts()
    print(f"recheck: {len(items)} items from {len(issues)} closed catalog bugs: "
          + ", ".join(f"{s} {sum(1 for x in items if x['status'] == s)}" for s in sorted({x['status'] for x in items})))


def drafts():
    """A comment per issue once every app on it has a verdict worth saying out loud. Never posted from here."""
    q = json.load(open(QUEUE))
    os.makedirs(DRAFTS, exist_ok=True)
    for n in sorted({x["issue"] for x in q["items"]}):
        xs = [x for x in q["items"] if x["issue"] == n]
        if any(x["status"] in ("pending", "different", "inconclusive", "not planned") for x in xs):
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
        head = "Verified by the FMA soak ✅" if ok else "Still reproduces after the fix ❌ — reopening suggested"
        body = f"{head}\n\n" + "\n".join(lines) + "\n"
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
    print(f"recheck {plat} host {host}: {len(todo)} closed bug(s) to rerun")
    for x in todo:
        print(f"   #{x['issue']} {x['slug']} ({x['kind']}), closed {x['closed']}")
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
        return ["No closed FMA catalog bugs yet."]
    mark = {"fixed": "✅ fixed", "still broken": "❌ still broken", "different": "⚠️ different findings",
            "inconclusive": "⚠️ inconclusive (title was already in the fleet)", "pending": "⏳ pending",
            "not planned": "➖ closed as not planned"}
    out = ["| Issue | App | Closed | Recheck | Record | Draft |", "|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda x: (x["status"] != "still broken", x["status"] != "different", -x["issue"])):
        draft = f"[comment](recheck/{x['issue']}.md)" if os.path.exists(os.path.join(DRAFTS, f"{x['issue']}.md")) else ""
        rec = f"[record]({x['record']})" if x.get("record") else ""
        out.append(f"| #{x['issue']} | {x['slug']} | {x['closed'][:10]} | {mark.get(x['status'], x['status'])} | {rec} | {draft} |")
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
