"""Task 4.1: the continuous soak loop for #55961.

Runs forever under launchd (KeepAlive restarts it if it dies). Each pass, per platform in parallel (one soak
host each): the steps in pass.sh (canaries, the update path, the daily random sample). Between passes it waits `loop.interval_hours`. Before each pass it checks qa.fleetdm.com answers and
the harness token still works; if not, it pauses and re-checks every `loop.pause_minutes` rather than filling
the results with outage noise. A pass where the instance dropped out partway is marked interrupted.

Every pass appends one line to results/loop/passes.jsonl (build, start/end, per-platform exit codes, status),
and results/loop/heartbeat.json says what the loop is doing right now, which is how the 72-hour check is read.

  python3 loop.py            # what launchd runs
  python3 loop.py --once     # one pass now, then exit (manual run)
"""
import argparse, datetime, json, os, subprocess, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, WS)
import settings
CFG = settings.load()
LOOP = {"interval_hours": 6, "pause_minutes": 15, **CFG.get("loop", {})}
OUT = os.path.join(WS, "results", "loop")
LOGS = os.path.join(WS, "logs")
LOCK = os.path.join(OUT, "loop.pid")


def wait_until(t):
    # short sleeps against the wall clock: one long time.sleep doesn't advance while the Mac sleeps, so a 1h40m
    # nap pushed the 10:19 pass back by 1h40m
    while time.time() < t:
        time.sleep(min(60, max(t - time.time(), 1)))


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def heartbeat(**kw):
    json.dump({"at": now(), "pid": os.getpid(), **kw}, open(os.path.join(OUT, "heartbeat.json"), "w"), indent=1)


def health():
    """(ok, build or reason). /version is public; a token check catches an expired or revoked harness token."""
    sys.path.insert(0, HERE)
    import runner
    try:
        b = runner.build()
    except Exception as e:
        return False, f"instance unreachable: {type(e).__name__}: {e}"
    st, d = runner.api("GET", "/me", tries=2)
    if st != 200:
        return False, f"harness token rejected ({st}): {d.get('message') if isinstance(d, dict) else d}"
    return True, b


def single_instance():
    """A second copy (launchd plus a manual run) would install on the same hosts at once and corrupt both runs."""
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK).read())
            os.kill(pid, 0)
            sys.exit(f"loop already running as pid {pid}")
        except (ValueError, ProcessLookupError):
            pass
    open(LOCK, "w").write(str(os.getpid()))


def clear_leftovers():
    """At startup only: a fresh process means the last one was killed (restart, crash, sleep), possibly mid-install.
    Its queued installs/uninstalls would sit ahead of this pass's and time its cycles out, so cancel the
    upcoming activities the harness user itself queued on the soak hosts. Anything queued by people is left alone.
    The apps those cycles touched stay marked harness-installed, so their next cycle uninstalls them."""
    sys.path.insert(0, HERE)
    import runner
    st, me = runner.api("GET", "/me", tries=2)
    uid = (me.get("user") or {}).get("id") if st == 200 else None
    if not uid:
        return []
    cancelled = []
    for hosts in CFG["hosts"].values():
        for h in hosts:
            st, d = runner.api("GET", f"/hosts/{h}/activities/upcoming?per_page=100")
            for a in d.get("activities") or []:
                if a.get("actor_id") == uid:
                    cst, _ = runner.api("DELETE", f"/hosts/{h}/activities/upcoming/{a['uuid']}", tries=2)
                    cancelled.append({"host": h, "type": a.get("type"),
                                      "title": (a.get("details") or {}).get("software_title"), "status": cst})
    return cancelled


def one_pass():
    ok, info = health()
    if not ok:
        return {"status": "paused", "reason": info}
    started, day = now(), datetime.date.today().isoformat()
    heartbeat(state="running", build=info, pass_started=started)
    procs = {}
    for plat in CFG["hosts"]:
        log = open(os.path.join(LOGS, f"loop-{day}-{plat}.log"), "a")
        log.write(f"\n=== pass {started} on {info}\n"); log.flush()
        # pass.sh holds the steps (canaries, update path, daily sample); the two hosts run side by side
        procs[plat] = (subprocess.Popen(["/bin/zsh", os.path.join(HERE, "pass.sh"), plat], cwd=HERE,
                                        stdout=log, stderr=subprocess.STDOUT), log)
    codes = {}
    for plat, (p, log) in procs.items():
        codes[plat] = p.wait()
        log.close()
    ok_after, info_after = health()
    rec = {"started": started, "finished": now(), "build": info, "exit_codes": codes,
           "status": "complete" if ok_after else "interrupted", **({} if ok_after else {"reason": info_after})}
    if ok_after and info_after != info:
        rec["build_changed_during_pass"] = info_after  # the digest's before/after must not mix these results
    with open(os.path.join(OUT, "passes.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True); os.makedirs(LOGS, exist_ok=True)
    single_instance()
    left = clear_leftovers()
    if left:
        print(f"{now()} cancelled leftover harness activities from the previous run: {json.dumps(left)}", flush=True)
    try:
        while True:
            rec = one_pass()
            print(f"{now()} {json.dumps(rec)}", flush=True)
            if a.once:
                return
            if rec["status"] == "paused":
                heartbeat(state="paused", reason=rec["reason"])
                wait_until(time.time() + LOOP["pause_minutes"] * 60)
                continue
            nxt = time.time() + LOOP["interval_hours"] * 3600 - (
                time.time() - datetime.datetime.fromisoformat(rec["started"]).timestamp())
            heartbeat(state="waiting", last_pass=rec, next_pass=datetime.datetime.fromtimestamp(max(nxt, time.time())).isoformat(timespec="seconds"))
            wait_until(max(nxt, time.time() + 60))
    finally:
        if os.path.exists(LOCK) and open(LOCK).read() == str(os.getpid()):
            os.remove(LOCK)


if __name__ == "__main__":
    main()
