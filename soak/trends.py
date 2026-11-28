"""Per-app trends: an app drifting toward failure before it fails (fma-soak plan, phase 1).

Pass/fail drives triage; the records also hold how long each step took and whether the first try was clean.
This compares each app with its own history on the same kind of host (macOS, Windows x64, Windows arm64: config
arch), so a slow ARM VM isn't drift. Not per host: the sampler spreads the catalog over the hosts, and 4,825 of the
5,776 app/host pairs had a single clean run on 2026-11-27, too few to compare.

- slower: a step's recent median (last RECENT clean runs) is SLOW_RATIO x its earlier median (up to BASE runs
  before those, at least MIN_BASE) and at least SLOW_MIN_SECONDS longer. Installs that creep toward the step
  timeout show up here weeks before they fail (#58442 was timed-out installs).
- failing more: of the app's last RECENT_TRIES first tries on the host, at least half had findings (reproduced or
  flaky), against at most BASE_FAIL_RATE before. A run of flaky results never reaches the digest's "needs a look"
  list on its own; together they are a trend.

Sample-based, not calendar-based: most apps run on one host about once a week (count 1), so a 7-day window would
hold one sample. Only apps that ran in the last RECENT_DAYS count, so hosts that left the soak drop out.
Installer size isn't in the records yet, so it isn't tracked.

  python3 trends.py           print today's flags
digest.py adds them as a "Trends" section.
"""
import datetime, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RECENT, BASE, MIN_BASE, RECENT_DAYS = 2, 8, 4, 14
SLOW_RATIO, SLOW_MIN_SECONDS = 3.0, 120
RECENT_TRIES, BASE_TRIES, MIN_BASE_TRIES, BASE_FAIL_RATE = 4, 12, 6, 0.10
# the step timings the records carry (2026-11-27: lifecycle cycles 7,793 clean, update_path 375)
STEPS = {"first_install_seconds": "first install", "second_install_seconds": "reinstall",
         "open_reinstall_seconds": "reinstall over the open app", "first_uninstall_seconds": "uninstall",
         "second_uninstall_seconds": "second uninstall",
         "old_install_seconds": "older-version install", "upgrade_install_seconds": "update over the open app",
         "cleanup_uninstall_seconds": "update-path uninstall"}


def group(r, arch):
    """The kind of host a record ran on: darwin, windows-x64 or windows-arm64 (arch = digest.ARCH)."""
    plat = r["slug"].rsplit("/", 1)[-1]
    if plat != "windows":
        return plat
    return "windows-arm64" if r["host"] in arch.get("arm64", set()) else "windows-x64"


def fresh(started, today=None):
    today = today or datetime.date.today()
    return started[:10] >= (today - datetime.timedelta(days=RECENT_DAYS)).isoformat()


def first_tries(recs):
    """Each cycle's first attempt: a record that isn't the retry of another."""
    return [r for r in recs if not r.get("retry_of")]


def slower(recs, arch, today=None):
    """[(slug, group, step label, earlier median s, recent median s, runs compared, recent hosts)] for clean runs
    that slowed."""
    out, series = [], {}
    for r in sorted(recs, key=lambda r: r["started"]):
        if r.get("outcome") != "pass":
            continue  # a failed step's time is the timeout, not the app
        for k, label in STEPS.items():
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 0:
                series.setdefault((r["slug"], group(r, arch), label), []).append((v, r["host"], r["started"]))
    for (slug, grp, label), xs in sorted(series.items()):
        recent, base = xs[-RECENT:], xs[-RECENT - BASE:-RECENT]
        if len(recent) < RECENT or len(base) < MIN_BASE or not fresh(recent[-1][2], today):
            continue
        was, now = statistics.median(x[0] for x in base), statistics.median(x[0] for x in recent)
        if now >= SLOW_RATIO * was and now - was >= SLOW_MIN_SECONDS:
            out.append((slug, grp, label, round(was), round(now), len(base) + len(recent),
                        sorted({x[1] for x in recent})))
    return out


def failing_more(recs, category, arch, today=None):
    """[(slug, group, recent fails, recent tries, earlier fails, earlier tries, recent hosts)].
    category = digest.category."""
    out, series = [], {}
    for r in sorted(first_tries(recs), key=lambda r: r["started"]):
        c = category(r)
        if c in ("pass", "findings", "flaky"):  # environment, harness, skipped and errors say nothing about the app
            series.setdefault((r["slug"], group(r, arch)), []).append((c != "pass", r["host"], r["started"]))
    for (slug, grp), xs in sorted(series.items()):
        recent, base = xs[-RECENT_TRIES:], xs[-RECENT_TRIES - BASE_TRIES:-RECENT_TRIES]
        if len(recent) < RECENT_TRIES or len(base) < MIN_BASE_TRIES or not fresh(recent[-1][2], today):
            continue
        rf, bf = sum(x[0] for x in recent), sum(x[0] for x in base)
        if rf * 2 >= len(recent) and bf <= BASE_FAIL_RATE * len(base):
            out.append((slug, grp, rf, len(recent), bf, len(base), sorted({x[1] for x in recent})))
    return out


def section(recs, category, arch, today=None):
    """Markdown lines for the digest, or [] when nothing drifted."""
    L = []
    on = lambda hosts: f" (recent runs on {', '.join(map(str, hosts))})"
    for slug, grp, label, was, now, n, hosts in slower(recs, arch, today):
        L.append(f"- {slug} ({grp}): {label} takes {now}s, up from {was}s (median of the last {RECENT} clean runs "
                 f"vs the {n - RECENT} before){on(hosts)}")
    for slug, grp, rf, rn, bf, bn, hosts in failing_more(recs, category, arch, today):
        L.append(f"- {slug} ({grp}): findings on {rf} of the last {rn} first tries, {bf} of {bn} before{on(hosts)}")
    return L


def load():
    """All records including first attempts (digest.all_records drops those), and digest's categories."""
    import digest
    import glob, json
    recs = []
    for p in glob.glob(os.path.join(digest.RES, "soak", "*", "*.json")):
        try:
            r = json.load(open(p))
        except (ValueError, OSError):
            continue
        if "slug" in r and "outcome" in r and "started" in r:
            recs.append(r)
    return recs, digest.category, digest.ARCH


if __name__ == "__main__":
    lines = section(*load())
    print("\n".join(lines) or "no drift")
