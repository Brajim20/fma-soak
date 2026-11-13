"""Task 4.2: coverage sampling + ledger for #55961.

Goal: priority apps on a fixed schedule, and the rest of the catalog a quarter per week, so all of it runs in a month.

Order of picks on each host:
- Reruns: apps listed in config `rerun.apps` go first, once each, until one of their lifecycle cycles started after
  `rerun.since` (e.g. apps whose findings came from a broken environment and that need a clean run).
- Priority tiers (config `priority.tiers`): each tier's apps get a real lifecycle cycle at least every `every_hours`
  (on any host). The most overdue go first. Each app's host rotates every period (owner() salted with the period
  number), so a host-specific failure shows up against another host next time; an offline pick hands it to a backup.
  Canaries are priority apps too when listed: their canary run counts.
- New apps: an app that shows up in the instance's catalog (results/sample/catalog-<plat>.json, kept by the report
  job) goes to the front of its host's next pass, once; after its first real cycle it's an ordinary app.
- Catch-up: until every app on the platform is covered, uncovered apps come before any repeat (version changes,
  blind spots, the quarter). Each one goes to exactly one host: its owner, or a backup (a second pick, hashed apart,
  with the owner left out). An offline owner's apps go to their backups, an offline backup's stay with the owner, and
  when both are online they split them, so a slow or offline host's share spreads over the others and nothing runs
  twice. Online is read from Fleet once per pass; pool slots count as online. A host tries each app once per pass.
- Version changes: ~`changed_share` of picks are apps whose version changed on main in the last 7 days and that
  haven't been cycled since, because a fresh upstream release is where breakage shows up first.
- Validator blind spots: ~`blind_share` of picks are apps the fleetdm/fleet validator checks least (hashless
  installers, its per-app version-check fallbacks, special-cased casks; listed by the daily audit as `blind_spots`)
  that haven't been cycled yet, oldest-covered first after that, since the soak's exists/patched checks are the only
  real coverage they get.
- The week's quarter (config `quarters`): every non-priority app has a fixed quarter (crc32 of the slug % count),
  and the week number since `quarters.epoch` (a Monday) picks which quarter runs. Each app in it gets one real cycle
  that week, on the host catch-up would give it, in a seeded order. Once this host's part of the quarter is done it
  runs version-changed and blind-spot apps only, then stops picking until the next pass.

- A broken stored installer (runner.store_problem) records the pick as environment; the app stays due. Three in a
  row stop the pass (store.py alerts).
- Invalid windows: config `coverage.invalid_windows` ({after, before, why}) are times the environment broke every
  install (qa's full installer store, #57132). A cycle started inside one that didn't pass doesn't count.
- Several hosts on a platform: each app belongs to one host (crc32 of the slug over the hosts that can run it, by
  random_sample.host_weights), so hosts never pick the same app. An app recorded as a known environment problem on
  one host (e.g. x64-only installers on the Windows ARM VM) goes to a host without that problem instead. Canaries and
  residents stay on the primary (first) host unless the primary can't run them. Each extra host keeps its own day
  files (<date>-<plat>-<host>.json); the primary keeps the original names.
- Pool slots (FMA_SOAK_POOL_<PLATFORM>, pool.py): ephemeral GitHub runners. Each slot owns a share like a host does
  (host_weights["pool"] per slot) and keeps its day files and ledger entries under the slot number, while the
  cycles run on whatever host ID the runner enrolled as this time. Records carry `pool_slot`.
- Time-boxed, not counted: it keeps picking until `--until` (pass.sh passes the start of the next pass). Each pick's
  choice and result are appended to results/sample/<date>-<plat>.json; the random choices are seeded by date, so a
  day's sequence can be replayed.

Canaries and known-environment apps are excluded. Each pick runs the lifecycle cycle (with the retry from 3.8).
A title the sample added to the soak fleet is removed again when its cycle ended with the app off the host; titles
that were already in the fleet are never removed.

The ledger (results/ledger.json) is rebuilt from every result record, so it can't drift from the evidence:
last outcome, catalog version, build and date per app and platform, the last real cycle (`last_cycled`), plus
coverage, the week's quarter and each priority tier's on-time count. Only the report job writes it (`--ledger`),
after every sampling job has saved. An app counts as covered once one of its lifecycle cycles really ran.

  python3 sample.py --platform darwin --until 2026-10-30T18:00   # sample until then
  python3 sample.py --platform darwin --count 5 --dry-run        # show the next picks without running them
  python3 sample.py --ledger                                     # rebuild the ledger and print coverage
"""
import argparse, datetime, glob, json, os, random, subprocess, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

SAMPLE_DIR = os.path.join(R.WS, "results", "sample")
LEDGER = os.path.join(R.WS, "results", "ledger.json")


def instance_catalog(plat):
    st, d = R.api("GET", f"/software/fleet_maintained_apps?fleet_id={R.FID}&per_page=5000")
    return sorted(a["slug"] for a in d.get("fleet_maintained_apps", []) if a["slug"].endswith("/" + plat))


def changed_last_7_days(plat):
    """Apps whose published version changed on main in the last 7 days. File touches alone don't count: the
    regular bulk catalog refresh rewrites nearly every manifest without changing its version."""
    git = lambda *a: subprocess.run(["git", "-C", R.REPO, *a], capture_output=True, text=True).stdout
    git("fetch", "-q", "origin", "main")
    old = git("rev-list", "-1", "--before=7.days", "origin/main").strip()
    if not old:
        return set()
    slugs, cur = set(), None
    for line in git("diff", "-U0", old, "origin/main", "--", "ee/maintained-apps/outputs").splitlines():
        if line.startswith("+++ b/"):
            parts = line[6:].split("/")
            cur = f"{parts[3]}/{plat}" if len(parts) == 5 and parts[4] == f"{plat}.json" else None
        elif cur and line.startswith(("+", "-")) and '"version":' in line:
            slugs.add(cur)
    return slugs


def blind_spots(plat):
    """The latest audit's list of apps the validator checks least (audit.py blind_spots); empty if none yet."""
    for p in sorted(glob.glob(os.path.join(R.WS, "results", "audit-*.json")), reverse=True):
        try:
            b = json.load(open(p)).get("blind_spots")
        except (OSError, ValueError):
            continue
        if b is not None:
            return {s: why for s, why in b.items() if s.endswith("/" + plat)}
    return {}


def hosts_for(plat):
    return R.CFG["hosts"][plat]


def known_env_hosts(slug):
    return {int(k.rsplit("@", 1)[1]) for k in R.CFG.get("known_environment", {}) if R.app_slug(k) == slug and k != slug}


def owner(plat, slug, without=(), salt=""):
    """The one host on this platform that samples this app, or None if no host can run it. Split by capacity
    (random_sample.host_weights, default 1): the primary spends most of its day on canaries, the update path and
    scenarios, so an even split would leave its share far behind the sample-only host's."""
    able = [h for h in all_hosts(plat) if h not in known_env_hosts(slug) and h not in without]
    if not able:
        return None
    w = R.CFG["random_sample"].get("host_weights", {})
    pool = set(pool_for(plat))
    weights = [max(float(w.get(str(h), w.get("pool", 1) if h in pool else 1)), 0) for h in able]
    x = (zlib.crc32((salt + slug).encode()) % 10000) / 10000 * sum(weights)
    for h, wt in zip(able, weights):
        if x < wt:
            return h
        x -= wt
    return able[-1]


def pool_for(plat):
    return R.CFG.get("pool", {}).get(plat, [])


def all_hosts(plat):
    """The platform's soak hosts plus its pool slots: everything that owns a share of the catalog."""
    return hosts_for(plat) + pool_for(plat)


def shard(plat, catalog, host):
    return [s for s in catalog if owner(plat, s) == host]


def suffix(plat, host):
    return "" if host == hosts_for(plat)[0] else f"-{host}"


def excluded(plat, host=None):
    host = host or hosts_for(plat)[0]
    primary = hosts_for(plat)[0]
    residents = {x for xs in R.CFG.get("watcher", {}).get("residents", {}).values() for x in xs}
    # canaries and residents are the primary's; another host takes one only when the primary can't run it
    held = {s for s in set(R.CFG["canaries"][plat]) | residents if host == primary or primary not in known_env_hosts(s)}
    return held | {R.app_slug(k) for k in R.CFG.get("known_environment", {}) if k.endswith(f"@{host}")} | R.busy_slugs()


def catalog_path(plat):
    return os.path.join(SAMPLE_DIR, f"catalog-{plat}.json")


def track_catalog(plat, cat):
    """results/sample/catalog-<plat>.json: the date each app first showed up in the instance's catalog. The first
    build marks everything already there as old (first_seen null). Only the report job calls this (via the ledger),
    so there's one writer."""
    p = catalog_path(plat)
    known = json.load(open(p))["first_seen"] if os.path.exists(p) else None
    today = datetime.date.today().isoformat()
    seen = {s: (known.get(s, today) if known is not None else None) for s in cat}
    gone = sorted(set(known or {}) - set(cat))
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    json.dump({"platform": plat, "first_seen": seen, "removed": gone}, open(p, "w"), indent=1)
    new = sorted(s for s, d in seen.items() if d == today and (known is None or s not in known))
    if new:
        print(f"catalog {plat}: {len(new)} new app(s): {', '.join(new)}")


def new_apps(plat, catalog, ledger):
    """Apps added to the catalog in the last `new_app_days` that haven't had a real cycle on any host yet, oldest
    addition first. Each gets up to `new_app_tries` picks (skips and environment problems use one up), then it waits
    for its quarter like every other app."""
    p = catalog_path(plat)
    if not os.path.exists(p):
        return []
    rs = R.CFG["random_sample"]
    since = (datetime.date.today() - datetime.timedelta(days=rs.get("new_app_days", 30))).isoformat()
    first = json.load(open(p))["first_seen"]
    out = []
    for s in catalog:
        d = first.get(s)
        if not d or d < since:
            continue
        es = [e for k, e in ledger.items() if R.app_slug(k) == s]
        if any(e.get("first_cycled") for e in es) or sum(e.get("lifecycle_runs", 0) for e in es) >= rs.get("new_app_tries", 3):
            continue
        out.append((d, s))
    return [s for _, s in sorted(out)]


def counts_as_cycle(outcome, started):
    """A lifecycle cycle that really ran: not a skip, harness error or environment problem, and not a failure inside
    a config coverage.invalid_windows window (every install broke there, whatever the app)."""
    o = str(outcome)
    if o.startswith(NOT_A_CYCLE) or not started:
        return False
    return o.startswith("pass") or not any(w["after"] <= started < w["before"]
                                           for w in R.CFG.get("coverage", {}).get("invalid_windows", []))


_UP = {}


def is_up(plat, host):
    """Online right now, read once per pass. A pool slot only exists while it runs, so it counts as up."""
    if host in pool_for(plat):
        return True
    if host not in _UP:
        _UP[host] = R.host_snapshot(host)["status"] == "online"
    return _UP[host]


def catch_up_host(plat, slug):
    """The one host that takes this uncovered app (see Catch-up above)."""
    first = owner(plat, slug)
    if first is None:
        return None
    second = owner(plat, slug, without={first}, salt="backup:")
    if second is None or not is_up(plat, second):
        return first
    if not is_up(plat, first):
        return second
    return first if zlib.crc32(("split:" + slug).encode()) % 2 == 0 else second


def catch_up(plat, full, ledger, host, skip, tried):
    """Uncovered apps on the whole platform that this host takes, in a seeded order."""
    covered = {R.app_slug(k) for k, e in ledger.items() if e.get("first_cycled")}
    out = [s for s in full if s not in covered and s not in skip and s not in tried and catch_up_host(plat, s) == host]
    random.Random(f"catch-up-{host}").shuffle(out)
    return out


def last_cycled_index(ledger):
    """slug -> the start of its newest real lifecycle cycle on any host ('' if none)."""
    out = {}
    for k, e in ledger.items():
        s = R.app_slug(k)
        out[s] = max(out.get(s, ""), e.get("last_cycled") or "")
    return out


def tiers(plat):
    """[(name, every_hours, slugs)] from config priority.tiers, this platform's apps only, in config order."""
    return [(t["name"], t["every_hours"], [s for s in t.get("apps", []) if s.endswith("/" + plat)])
            for t in R.CFG.get("priority", {}).get("tiers", [])]


def priority_apps(plat):
    return {s for _, _, xs in tiers(plat) for s in xs}


def tier_host(plat, slug, hours, now):
    """The host that runs this priority app in the current period (`now`, epoch seconds). The pick rotates with the
    period so the app doesn't always land on the same host; an offline pick hands it to a backup."""
    period = int(now // (hours * 3600))
    first = owner(plat, slug, salt=f"tier{period}:")
    if first is None or is_up(plat, first):
        return first
    second = owner(plat, slug, without={first}, salt=f"tier{period}:backup:")
    return second if second is not None and is_up(plat, second) else first


def hours_since(iso, now):
    return (now - datetime.datetime.fromisoformat(iso)).total_seconds() / 3600 if iso else None


def priority_due(plat, full, ledger, host, skip, tried, now=None):
    """[(slug, why)]: this host's priority apps whose last real cycle (any host) is at least their tier's every_hours
    old, less priority.slack_hours so a pass that starts a little early still takes them. Most overdue first."""
    now = now or datetime.datetime.now()
    slack = R.CFG.get("priority", {}).get("slack_hours", 1)
    last, full, out = last_cycled_index(ledger), set(full), []
    for name, hours, apps in tiers(plat):
        for s in apps:
            if s not in full or s in skip or s in tried or tier_host(plat, s, hours, now.timestamp()) != host:
                continue
            age = hours_since(last.get(s), now)
            if age is None or age >= hours - slack:
                out.append((-(age / hours) if age is not None else -1e9, s, name, age))
    out.sort()
    return [(s, f"priority {name}: " + (f"last cycled {age:.0f} h ago" if age is not None else "never cycled"))
            for _, s, name, age in out]


def week_quarter(now=None):
    """(quarter index, the Monday this week started): weeks since quarters.epoch, mod quarters.count."""
    q = R.CFG.get("quarters", {})
    today = (now or datetime.datetime.now()).date()
    start = today - datetime.timedelta(days=today.weekday())
    epoch = datetime.date.fromisoformat(q.get("epoch", "2026-11-16"))
    return ((start - epoch).days // 7) % q.get("count", 4), start.isoformat()


def quarter_of(slug):
    return zlib.crc32(("quarter:" + slug).encode()) % R.CFG.get("quarters", {}).get("count", 4)


def quarter_todo(plat, full, ledger, host, skip, tried, now=None):
    """This week's quarter apps that this host takes (the catch-up split, so an offline host's part moves to its
    backups) and that had no real cycle on any host since the week began, in an order seeded by week and host."""
    q, start = week_quarter(now)
    last, pri = last_cycled_index(ledger), priority_apps(plat)
    out = [s for s in full if quarter_of(s) == q and s not in pri and s not in skip and s not in tried
           and last.get(s, "") < start and catch_up_host(plat, s) == host]
    random.Random(f"quarter-{start}-{host}").shuffle(out)
    return out


def next_pick(plat, catalog, changed, ledger, rng, host, blind=None, full=None, tried=(), now=None):
    """(slug, why) or None: reruns, overdue priority apps, apps new to the catalog, uncovered apps, then version
    changes and blind spots by share, then the week's quarter; with the quarter done, version changes and blind
    spots only."""
    full = full or catalog
    week_ago = ((now or datetime.datetime.now()) - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    skip = excluded(plat, host)
    rr = R.CFG.get("rerun", {})
    for s in rr.get("apps", []):
        last = max((((ledger.get(f"{s}@{h}") or {}).get("last_lifecycle") or {}).get("date", "") for h in all_hosts(plat)),
                   default="")
        if s in set(catalog) and s not in skip and last < rr.get("since", ""):
            return s, "rerun: " + rr.get("why", "listed in config rerun")
    due = priority_due(plat, full, ledger, host, skip, tried, now)
    if due:
        return due[0]
    fresh = [s for s in new_apps(plat, catalog, ledger) if s not in skip]
    if fresh:
        return fresh[0], "new to the catalog"
    due = catch_up(plat, full, ledger, host, skip, tried)
    if due:
        return due[0], "catch-up: not covered on any host yet"
    last = lambda s: ((ledger.get(f"{s}@{host}") or {}).get("last_lifecycle") or {}).get("date", "")
    def version_changed():
        due = sorted(s for s in changed if s in set(catalog) and s not in skip and s not in tried and last(s) < week_ago)
        return (rng.choice(due), "version changed in the last 7 days") if due else None
    def blind_spot():
        # never-cycled first, then the one whose last cycle is oldest; nothing cycled in the last 7 days
        due = [s for s in sorted(blind or {}) if s in set(catalog) and s not in skip and s not in tried and last(s) < week_ago]
        if not due:
            return None
        oldest = min(last(s) for s in due)
        s = rng.choice([x for x in due if last(x) == oldest])  # seeded, not alphabetical
        return s, "validator blind spot (" + ", ".join(blind[s]) + ")"
    if rng.random() < R.CFG["random_sample"].get("changed_share", 0.3) and (p := version_changed()):
        return p
    if blind and rng.random() < R.CFG["random_sample"].get("blind_share", 0.2) and (p := blind_spot()):
        return p
    todo = quarter_todo(plat, full, ledger, host, skip, tried, now)
    if todo:
        q, start = week_quarter(now)
        return todo[0], f"quarter {q + 1} (week of {start})"
    return version_changed() or blind_spot()


def entry_now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def remove_if_added(slug, host, rec):
    """Only titles this harness added, only when the app is confirmed off the host."""
    state = R.load_state(host)
    tid = state["harness_titles"].get(slug)
    if slug in {x for xs in R.CFG.get("watcher", {}).get("residents", {}).values() for x in xs}:
        return "kept: watcher resident"
    if not tid:
        return "kept: not added by the harness"
    if state["harness_installed"].get(f"{slug}@{host}"):
        return "kept: app may still be on the host"
    st, d = R.api("DELETE", f"/software/titles/{tid}/available_for_install?fleet_id={R.FID}")
    if st in (200, 204):
        state["harness_titles"].pop(slug, None)
        R.save_state(state, host)
        return "removed"
    return f"kept: delete returned {st} {d.get('message') if isinstance(d, dict) else ''}"


def run_sample(plat, until, count, dry, host=None, slot=None):
    """`slot`: a pool slot (a GitHub runner); its share, day file and ledger entries go by the slot number,
    the cycles run on `host`, the ID the runner enrolled as this run."""
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    host = host or hosts_for(plat)[0]
    if slot is not None and slot not in pool_for(plat):
        sys.exit(f"{slot} isn't a {plat} pool slot ({pool_for(plat)})")
    if slot is None and host not in hosts_for(plat):
        sys.exit(f"host {host} isn't a {plat} soak host ({hosts_for(plat)})")
    me = host if slot is None else slot
    full = instance_catalog(plat)
    catalog = shard(plat, full, me)
    tried = set()
    changed, blind = changed_last_7_days(plat), blind_spots(plat)
    q, week = week_quarter()
    ledger = build_ledger()["apps"]
    today = datetime.date.today().isoformat()
    day_path = os.path.join(SAMPLE_DIR, f"{today}-{plat}{suffix(plat, me)}.json")
    day = json.load(open(day_path)) if os.path.exists(day_path) else {
        "date": today, "platform": plat, "host": me, "day_seed": f"{today}-{plat}", "picks": []}
    # seeded by the date and how many picks today already has, so a resumed day continues the same sequence
    rng = random.Random(f"{day['day_seed']}-{len(day['picks'])}")
    print(f"sample {plat} host {host}{f' (pool slot {slot})' if slot is not None else ''} ({len(catalog)} apps are this host's): quarter {q + 1} (week of {week}); "
          f"{len(priority_apps(plat))} priority apps; {len(changed)} version changes in 7 days; {len(blind)} validator blind spots; until {until or '-'}; count {count or '-'}")
    # don't start a cycle the deadline can't hold: most finish in minutes, but p90 is 30-45 min (Windows on ARM, slow
    # installer downloads), and a pass that overruns delays the next one
    stop = until - datetime.timedelta(minutes=R.CFG["loop"].get("cycle_budget_minutes", 30)) if until else None
    n = broken = 0
    while (not stop or datetime.datetime.now() < stop) and (not count or n < count):
        if not R.wait_online(host):
            print(f"sample {plat}: stopping, host {host} is offline")
            break
        if n == 0 and slot is not None and not dry and not R.answers_live_query(host):
            print(f"sample {plat}: stopping, pool runner {host} doesn't answer live queries")
            break
        pick = next_pick(plat, catalog, changed, ledger, rng, me, blind, full, tried)
        if not pick:
            break
        slug, why = pick
        tried.add(slug)
        n += 1
        if dry:
            print(f"   would pick {slug} ({why})")
            changed.discard(slug)
            ledger[f"{slug}@{me}"] = {"last_lifecycle": {"date": entry_now(), "outcome": "dry-run"},
                                      "first_cycled": entry_now(), "last_cycled": entry_now()}
            continue
        entry = {"slug": slug, "why": why, "started": datetime.datetime.now().isoformat(timespec="seconds")}
        try:
            rec = R.run(R.cycle, slug, host)
            if slot is not None and rec.get("record"):
                rec["pool_slot"] = slot
                json.dump(rec, open(rec["record"], "w"), indent=1)
            entry.update(outcome=rec.get("outcome"), record=rec.get("record"), cleanup=remove_if_added(slug, host, rec))
        except Exception as e:
            entry["outcome"] = f"error: {type(e).__name__}: {e}"
        print(f"   {slug} ({why}): {entry['outcome']} [{entry.get('cleanup', '')}]", flush=True)
        day["picks"].append(entry)
        json.dump(day, open(day_path, "w"), indent=1)
        if str(entry["outcome"]).startswith(R.STORE_BROKEN):
            # not this app's turn used up: it stays due for when the store is fixed. Three in a row means the
            # store is broken, not one file, and every pick would end the same way
            broken += 1
            if broken >= 3:
                print(f"sample {plat}: stopping, the instance served 3 broken installers in a row (store.py alerts)")
                break
            continue
        broken = 0
        e = ledger.setdefault(f"{slug}@{me}", {"lifecycle_runs": 0, "update_runs": 0})
        e["last_lifecycle"] = {"date": entry["started"], "outcome": entry["outcome"]}
        e["lifecycle_runs"] = e.get("lifecycle_runs", 0) + 1
        if counts_as_cycle(entry["outcome"], entry["started"]):
            e["first_cycled"] = e.get("first_cycled") or entry["started"]
            e["last_cycled"] = entry["started"]
        changed.discard(slug)
    # no ledger write here: several jobs sample at once and each would push its own partial view; the report job
    # rebuilds it from every record after all of them have saved


NOT_A_CYCLE = ("skipped", "error", "environment")


def is_covered(e):
    """Covered = at least one lifecycle cycle actually ran, ever. A later skip, harness error or environment
    problem (host restarted, queue busy) doesn't take that back."""
    return bool(e.get("first_cycled"))


def schedule(plat, cat, apps, nope, now=None):
    """The week's quarter (apps cycled since the week began, of the apps in it) and, per priority tier, the apps
    cycled within their every_hours plus priority.grace_hours (a pass's length: due apps wait for the next pass on
    their host) and the ones overdue."""
    now = now or datetime.datetime.now()
    last, pri = last_cycled_index(apps), priority_apps(plat)
    q, start = week_quarter(now)
    qs = [s for s in cat if quarter_of(s) == q and s not in pri and s not in nope]
    out = {"quarter": {"index": q + 1, "of": R.CFG.get("quarters", {}).get("count", 4), "week_start": start,
                       "apps": len(qs), "done": sum(1 for s in qs if last.get(s, "") >= start)}, "priority": []}
    grace = R.CFG.get("priority", {}).get("grace_hours", 6)
    for name, hours, xs in tiers(plat):
        xs = [s for s in xs if not cat or (s in cat and s not in nope)]
        late = {s: hours_since(last.get(s), now) for s in xs}
        late = {s: a for s, a in late.items() if a is None or a > hours + grace}
        out["priority"].append({"name": name, "every_hours": hours, "apps": len(xs), "on_time": len(xs) - len(late),
                                "overdue": {s: (round(a) if a is not None else None) for s, a in sorted(late.items())}})
    return out


def build_ledger(write=False):
    apps = {}
    for p in sorted(glob.glob(os.path.join(R.WS, "results", "soak", "*", "*.json"))):
        try:
            r = json.load(open(p))
        except (ValueError, OSError):
            continue
        if "slug" not in r or "outcome" not in r:
            continue
        e = apps.setdefault(f"{r['slug']}@{r.get('pool_slot') or r['host']}", {"lifecycle_runs": 0, "update_runs": 0})
        kind = "update" if r.get("kind") in ("update_path", "patch_when_closed") else "lifecycle"
        e[f"{kind}_runs"] += 1
        if kind == "lifecycle" and counts_as_cycle(r["outcome"], r.get("started")):
            e["first_cycled"] = min(e.get("first_cycled") or r["started"], r["started"])
            e["last_cycled"] = max(e.get("last_cycled") or r["started"], r["started"])
        e[f"last_{kind}"] = {"outcome": r["outcome"], "date": r.get("started"), "build": r.get("build"),
                             "catalog_version": r.get("catalog_version"), "record": os.path.relpath(p, R.WS),
                             "findings": [f["msg"] for f in r.get("findings", [])]}
    coverage = {}
    for plat in R.CFG["hosts"]:
        cat = instance_catalog(plat) if write else None
        total = len(cat) if write else None
        if write:
            track_catalog(plat, cat)
        # an app counts once, whichever of the platform's hosts covered it
        n = len({R.app_slug(k) for k, e in apps.items() if R.app_slug(k).endswith("/" + plat) and is_covered(e)})
        # apps no host can run (known_environment on every host, e.g. Edge) are listed, not counted as missing
        nope = sorted(s for s in cat if owner(plat, s) is None) if write else []
        total = total - len(nope) if write else None
        coverage[plat] = {"covered": n, "catalog": total, "percent": round(100 * n / total, 1) if total else None,
                          "not_coverable": nope}
        coverage[plat].update(schedule(plat, cat or [], apps, set(nope)))
    led = {"built": datetime.datetime.now().isoformat(timespec="seconds"), "coverage": coverage, "apps": apps}
    if write:
        json.dump(led, open(LEDGER, "w"), indent=1)
        print("coverage:", json.dumps(coverage))
    return led


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["darwin", "windows"])
    ap.add_argument("--until", type=datetime.datetime.fromisoformat, help="stop picking after this local time")
    ap.add_argument("--count", type=int, help="stop after this many picks")
    ap.add_argument("--host", type=int, help="a second host on the platform (default: the primary)")
    ap.add_argument("--pool-slot", type=int, help="the pool slot this runner is (pool.py); --host is its host ID")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    a = ap.parse_args()
    if a.ledger:
        build_ledger(write=True)
    elif a.platform and (a.until or a.count):
        run_sample(a.platform, a.until, a.count, a.dry_run, a.host, a.pool_slot)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
