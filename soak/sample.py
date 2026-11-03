"""Task 4.2: coverage sampling + ledger for #55961.

Goal: every FMA in the catalog gets a lifecycle cycle, in random order, with no app repeated before all are done.

- The deck: per platform, the catalog in a seeded random order (results/sample/deck-<plat>.json). Picks come off
  the top, skipping apps already covered this round, so every app comes up exactly once per round. When the
  deck runs out, round N+1 reshuffles with a new seed. Apps new to the catalog are inserted at a random spot.
- Version changes: ~`changed_share` of picks are apps whose version changed on main in the last 7 days and that
  haven't been cycled since, because a fresh upstream release is where breakage shows up first.
- Validator blind spots: ~`blind_share` of picks are apps the fleetdm/fleet validator checks least (hashless
  installers, its per-app version-check fallbacks, special-cased casks; listed by the daily audit as `blind_spots`)
  that haven't been cycled yet, oldest-covered first after that, since the soak's exists/patched checks are the only
  real coverage they get.
- Several hosts on a platform: each app belongs to one host (crc32 of the slug over the hosts that can run it), so
  hosts never pick the same app. An app recorded as a known environment problem on one host (e.g. x64-only
  installers on the Windows ARM VM) goes to a host without that problem instead. Canaries and residents stay on the
  primary (first) host unless the primary can't run them. Each extra host keeps its own deck and day files
  (deck-<plat>-<host>.json, <date>-<plat>-<host>.json); the primary keeps the original names.
- Time-boxed, not counted: it keeps picking until `--until` (pass.sh passes the start of the next pass), so the
  daily count follows real speed. Each pick's choice and result are appended to results/sample/<date>-<plat>.json;
  the random choices are seeded by date, so a day's sequence can be replayed.

Canaries and known-environment apps are excluded. Each pick runs the lifecycle cycle (with the retry from 3.8).
A title the sample added to the soak fleet is removed again when its cycle ended with the app off the host; titles
that were already in the fleet are never removed.

The ledger (results/ledger.json) is rebuilt from every result record, so it can't drift from the evidence:
last outcome, catalog version, build and date per app and platform, plus coverage and deck progress.

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


def owner(plat, slug):
    """The one host on this platform that samples this app, or None if no host can run it. Split by capacity
    (random_sample.host_weights, default 1): the primary spends most of its day on canaries, the update path and
    scenarios, so an even split would leave its share far behind the sample-only host's."""
    able = [h for h in hosts_for(plat) if h not in known_env_hosts(slug)]
    if not able:
        return None
    w = R.CFG["random_sample"].get("host_weights", {})
    weights = [max(float(w.get(str(h), 1)), 0) for h in able]
    x = (zlib.crc32(slug.encode()) % 10000) / 10000 * sum(weights)
    for h, wt in zip(able, weights):
        if x < wt:
            return h
        x -= wt
    return able[-1]


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


def load_deck(plat, catalog, host=None):
    """The shuffled catalog (this host's share of it) for this round; new catalog apps go in at a random spot,
    removed ones (or ones now another host's) drop out."""
    host = host or hosts_for(plat)[0]
    path = os.path.join(SAMPLE_DIR, f"deck-{plat}{suffix(plat, host)}.json")
    if os.path.exists(path):
        deck = json.load(open(path))
    else:
        deck = new_round(plat, catalog, 1, host)
    known = set(deck["order"])
    rng = random.Random(f"{deck['seed']}-{len(known)}")
    for slug in sorted(set(catalog) - known):
        deck["order"].insert(rng.randrange(len(deck["order"]) + 1), slug)
    deck["order"] = [x for x in deck["order"] if x in set(catalog)]
    return deck


def new_round(plat, catalog, n, host=None):
    host = host or hosts_for(plat)[0]
    seed = int(datetime.date.today().strftime("%Y%m%d")) * 100 + n * 10 + (1 if plat == "darwin" else 2)
    if suffix(plat, host):
        seed = seed * 1000 + host % 1000
    order = sorted(catalog)
    random.Random(seed).shuffle(order)
    return {"platform": plat, "host": host, "round": n, "seed": seed, "started": datetime.date.today().isoformat(),
            "order": order, "done": []}


def save_deck(deck):
    host = deck.get("host") or hosts_for(deck["platform"])[0]
    json.dump(deck, open(os.path.join(SAMPLE_DIR, f"deck-{deck['platform']}{suffix(deck['platform'], host)}.json"), "w"), indent=1)


def next_pick(plat, deck, catalog, changed, ledger, rng, host, blind=None):
    """(slug, why) or None. A version-changed app is due if it hasn't been cycled since the change window began."""
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    skip = excluded(plat, host)
    if rng.random() < R.CFG["random_sample"].get("changed_share", 0.3):
        due = sorted(s for s in changed if s in set(catalog) and s not in skip and
                     ((ledger.get(f"{s}@{host}") or {}).get("last_lifecycle") or {}).get("date", "") < week_ago)
        if due:
            return rng.choice(due), "version changed in the last 7 days"
    if blind and rng.random() < R.CFG["random_sample"].get("blind_share", 0.2):
        # never-cycled first, then the one whose last cycle is oldest; nothing cycled in the last 7 days
        last = lambda s: ((ledger.get(f"{s}@{host}") or {}).get("last_lifecycle") or {}).get("date", "")
        due = [s for s in sorted(blind) if s in set(catalog) and s not in skip and last(s) < week_ago]
        if due:
            oldest = min(last(s) for s in due)
            s = rng.choice([x for x in due if last(x) == oldest])  # seeded, not alphabetical
            return s, "validator blind spot (" + ", ".join(blind[s]) + ")"
    for _ in range(2):
        for slug in deck["order"]:
            if slug in deck["done"] or slug in skip:
                continue
            if max((((ledger.get(f"{slug}@{h}") or {}).get("last_lifecycle") or {}).get("date", "") for h in hosts_for(plat)),
                   default="") >= deck["started"]:
                deck["done"].append(slug)  # already cycled this round some other way (canary, manual run)
                continue
            return slug, f"deck round {deck['round']}"
        deck.update(new_round(plat, catalog, deck["round"] + 1, host))  # every app done this round: reshuffle
    return None


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


def run_sample(plat, until, count, dry, host=None):
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    host = host or hosts_for(plat)[0]
    if host not in hosts_for(plat):
        sys.exit(f"host {host} isn't a {plat} soak host ({hosts_for(plat)})")
    catalog = shard(plat, instance_catalog(plat), host)
    deck, changed, blind = load_deck(plat, catalog, host), changed_last_7_days(plat), blind_spots(plat)
    ledger = build_ledger()["apps"]
    today = datetime.date.today().isoformat()
    day_path = os.path.join(SAMPLE_DIR, f"{today}-{plat}{suffix(plat, host)}.json")
    day = json.load(open(day_path)) if os.path.exists(day_path) else {
        "date": today, "platform": plat, "host": host, "day_seed": f"{today}-{plat}", "picks": []}
    # seeded by the date and how many picks today already has, so a resumed day continues the same sequence
    rng = random.Random(f"{day['day_seed']}-{len(day['picks'])}")
    print(f"sample {plat} host {host} ({len(catalog)} apps are this host's): deck round {deck['round']} (seed {deck['seed']}), {len(deck['done'])}/{len(deck['order'])} "
          f"done; {len(changed)} version changes in 7 days; {len(blind)} validator blind spots; until {until or '-'}; count {count or '-'}")
    # don't start a cycle the deadline can't hold: most finish in minutes, but p90 is 30-45 min (Windows on ARM, slow
    # installer downloads), and a pass that overruns delays the next one
    stop = until - datetime.timedelta(minutes=R.CFG["loop"].get("cycle_budget_minutes", 30)) if until else None
    n = 0
    while (not stop or datetime.datetime.now() < stop) and (not count or n < count):
        if not R.wait_online(host):
            print(f"sample {plat}: stopping, host {host} is offline")
            break
        pick = next_pick(plat, deck, catalog, changed, ledger, rng, host, blind)
        if not pick:
            break
        slug, why = pick
        n += 1
        if dry:
            print(f"   would pick {slug} ({why})")
            deck["done"].append(slug) if why.startswith("deck") else changed.discard(slug)
            ledger[f"{slug}@{host}"] = {"last_lifecycle": {"date": entry_now(), "outcome": "dry-run"}}
            continue
        entry = {"slug": slug, "why": why, "started": datetime.datetime.now().isoformat(timespec="seconds")}
        try:
            rec = R.run(R.cycle, slug, host)
            entry.update(outcome=rec.get("outcome"), record=rec.get("record"), cleanup=remove_if_added(slug, host, rec))
        except Exception as e:
            entry["outcome"] = f"error: {type(e).__name__}: {e}"
        print(f"   {slug} ({why}): {entry['outcome']} [{entry.get('cleanup', '')}]", flush=True)
        day["picks"].append(entry)
        json.dump(day, open(day_path, "w"), indent=1)
        ledger[f"{slug}@{host}"] = {"last_lifecycle": {"date": entry["started"], "outcome": entry["outcome"]}}
        changed.discard(slug)
        if slug not in deck["done"]:
            deck["done"].append(slug)  # a version-change pick counts for the round too
        save_deck(deck)
    if not dry:
        build_ledger(write=True)


def is_covered(e):
    """Covered = at least one lifecycle cycle actually ran (a skip or a harness error doesn't count)."""
    return not str((e.get("last_lifecycle") or {}).get("outcome", "skipped")).startswith(("skipped", "error"))


def build_ledger(write=False):
    apps = {}
    for p in sorted(glob.glob(os.path.join(R.WS, "results", "soak", "*", "*.json"))):
        try:
            r = json.load(open(p))
        except (ValueError, OSError):
            continue
        if "slug" not in r or "outcome" not in r:
            continue
        e = apps.setdefault(f"{r['slug']}@{r['host']}", {"lifecycle_runs": 0, "update_runs": 0})
        kind = "update" if r.get("kind") in ("update_path", "patch_when_closed") else "lifecycle"
        e[f"{kind}_runs"] += 1
        e[f"last_{kind}"] = {"outcome": r["outcome"], "date": r.get("started"), "build": r.get("build"),
                             "catalog_version": r.get("catalog_version"), "record": os.path.relpath(p, R.WS),
                             "findings": [f["msg"] for f in r.get("findings", [])]}
    coverage = {}
    for plat, hosts in R.CFG["hosts"].items():
        total = len(instance_catalog(plat)) if write else None
        # an app counts once, whichever of the platform's hosts covered it
        n = len({k.split("@")[0] for k, e in apps.items() if k.split("@")[0].endswith("/" + plat) and is_covered(e)})
        coverage[plat] = {"covered": n, "catalog": total, "percent": round(100 * n / total, 1) if total else None}
        for h in hosts:
            dp = os.path.join(SAMPLE_DIR, f"deck-{plat}{suffix(plat, h)}.json")
            if os.path.exists(dp):
                dk = json.load(open(dp))
                d = {"round": dk["round"], "done": len(dk["done"]), "of": len(dk["order"])}
                coverage[plat]["deck"] = d if h == hosts[0] else coverage[plat].get("deck")
                coverage[plat].setdefault("decks", {})[str(h)] = d
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
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    a = ap.parse_args()
    if a.ledger:
        build_ledger(write=True)
    elif a.platform and (a.until or a.count):
        run_sample(a.platform, a.until, a.count, a.dry_run, a.host)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
