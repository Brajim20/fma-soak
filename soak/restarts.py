"""Restart on demand: a Windows installer that needs a restart blocks the app on that host until it gets one.

Google Drive's uninstall leaves a restart pending (3010), and every later install on that host fails with 1610 until
the host restarts. The soak VMs never restart on their own, so Google Drive failed 64 times on all five Windows hosts
2026-11-15..20, and a handful of apps (SQL Server Management Studio, Omnissa Horizon, Visual Studio, PreForm,
MindManager) ended their installs with 3010 too.

After a cycle whose install or uninstall output says a restart is needed (runner.restart_needed):
  1. on a host listed in restart.hosts with restarts left today (restart.max_per_day), wait for its queue to empty,
     restart it through MDM (stuck.restart: RebootNow), wait until its own uptime shows the restart plus
     loop.settle_minutes, and return True so the sampler reruns the app once (uninstall, restart, reinstall is what
     a customer does);
  2. otherwise (host not listed, no restarts left, or it didn't come back as restarted), block the app on this host
     until Fleet shows a restart after the block (blocked()).

State per host in results/state/restart-<host>.json, written only by the job that cycles that host:
  {"history": [ISO UTC restart times], "blocked": {slug: ISO UTC since}}
"""
import datetime, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R


def path(host):
    return os.path.join(R.WS, "results", "state", f"restart-{host}.json")


def load(host):
    try:
        return json.load(open(path(host)))
    except (OSError, ValueError):
        return {"history": [], "blocked": {}}


def save(host, s):
    os.makedirs(os.path.dirname(path(host)), exist_ok=True)
    json.dump(s, open(path(host), "w"), indent=1)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def cfg():
    return R.CFG.get("restart", {})


def restarts_today(s, now=None):
    today = (now or now_utc()).date().isoformat()
    return sum(1 for t in s.get("history", []) if t[:10] == today)


def uptime(host):
    st, d = R.api("POST", f"/hosts/{host}/query", {"query": "SELECT total_seconds FROM uptime;"}, timeout=120)
    rows = d.get("rows") or [] if st == 200 and not d.get("error") else []
    v = str(rows[0].get("total_seconds", "")) if rows else ""
    return int(v) if v.isdigit() else None


def blocked(host, now=None):
    """Slugs blocked on this host by a restart it hasn't had yet. A block ends once Fleet's last_restarted_at is
    after it (that field lags up to ~1 h behind a real restart, which only delays the unblock)."""
    s = load(host)
    if not s.get("blocked"):
        return set()
    st, d = R.api("GET", f"/hosts/{host}")
    last = R.utc(((d.get("host") or {}).get("last_restarted_at")) if st == 200 else None)
    keep = {k: v for k, v in s["blocked"].items() if not (last and last > R.utc(v))}
    if keep != s["blocked"]:
        s["blocked"] = keep
        save(host, s)
    return set(keep)


def block(host, slug, why):
    s = load(host)
    s.setdefault("blocked", {})[slug] = now_utc().isoformat(timespec="seconds")
    save(host, s)
    print(f"   {slug}: blocked on {host} until it restarts ({why})")


def handle(host, slug, plat):
    """After a cycle that needs a restart: True when the host was restarted and is settled (rerun the app),
    False when the app was blocked on the host instead."""
    c = cfg()
    if plat != "windows" or host not in c.get("hosts", []):
        block(host, slug, "host not in restart.hosts")
        return False
    s = load(host)
    if restarts_today(s) >= c.get("max_per_day", 2):
        block(host, slug, f"already restarted {restarts_today(s)} times today (restart.max_per_day)")
        return False
    import stuck
    R.queue_idle(host)
    before = uptime(host)
    sent = stuck.restart(host)
    print(f"   {slug} needs a restart: MDM restart of {host}: {sent} (uptime before {before}s)")
    if sent != "sent":
        block(host, slug, f"restart not sent: {sent}")
        return False
    s["history"] = (s.get("history", []) + [now_utc().isoformat(timespec="seconds")])[-50:]
    save(host, s)
    # restarted = it answers with an uptime smaller than before (or, without a before, under 15 min)
    end, up = time.time() + c.get("wait_minutes", 25) * 60, None
    time.sleep(120)
    while time.time() < end:
        up = uptime(host)
        if up is not None and (up < before if before else up < 900):
            break
        time.sleep(60)
    else:
        block(host, slug, f"no restart seen within {c.get('wait_minutes', 25)} min (uptime {up})")
        return False
    need = R.CFG.get("loop", {}).get("settle_minutes", 10) * 60
    if up < need:
        print(f"   {host} restarted (up {up // 60} min): waiting {(need - up) // 60 + 1} min for it to settle")
        time.sleep(need - up)
    return R.wait_online(host)
