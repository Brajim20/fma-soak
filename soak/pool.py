"""Pool runners: GitHub macOS runners that join the soak for one job and leave again.

Each runner names itself fma-pool-<slot>-<run id>-<attempt>, installs fleetd with the soak fleet's enroll secret,
samples its slot's share of new apps (sample.py --pool-slot) and deletes its host from Fleet when the job ends. The
runner's host ID is new every run; the slot number is what owns the share, the deck and the ledger entries.

Only hosts this module can prove are its own are ever deleted: in the soak fleet, named fma-pool-..., and not one of
the configured soak hosts.

  python3 pool.py wait --hostname fma-pool-90001-123-1     # print the host ID once it has enrolled
  python3 pool.py finish --host 650 --hostname fma-pool-90001-123-1
  python3 pool.py sweep                                     # delete pool hosts a killed job left behind
"""
import argparse, datetime, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

NAME = re.compile(r"^fma-pool-\d+-\d+-\d+(\.local)?$")


def static_hosts():
    return {h for hs in R.CFG["hosts"].values() for h in hs}


def pool_hosts():
    st, d = R.api("GET", f"/hosts?fleet_id={R.FID}&query=fma-pool-&per_page=500")
    if st != 200:
        sys.exit(f"listing hosts returned {st}: {d}")
    return [h for h in d.get("hosts", []) if NAME.match(h.get("hostname", "")) and h["id"] not in static_hosts()]


def ours(host, hostname=None):
    """The host record, if it is one of ours (and, with hostname, that exact runner); otherwise None."""
    st, d = R.api("GET", f"/hosts/{host}")
    h = d.get("host") if st == 200 else None
    if not h or h.get("team_id") != R.FID or h["id"] in static_hosts() or not NAME.match(h.get("hostname", "")):
        return None
    if hostname and h["hostname"].removesuffix(".local") != hostname:
        return None
    return h


def wait(hostname, minutes=15):
    end = time.time() + minutes * 60
    while time.time() < end:
        found = [h for h in pool_hosts() if h["hostname"].removesuffix(".local") == hostname]
        if len(found) == 1:
            print(found[0]["id"])
            return
        if len(found) > 1:
            sys.exit(f"{len(found)} hosts named {hostname}: {[h['id'] for h in found]}")
        time.sleep(20)
    sys.exit(f"{hostname} didn't enroll in fleet {R.FID} within {minutes} min")


def drop_titles(host):
    """Titles this host's runs added and couldn't remove (the app was still on it). The VM is about to go, so the
    app goes with it; a title another host's state also holds stays."""
    state = R.load_state(host)
    others = set()
    for f in os.listdir(os.path.dirname(R.state_path(host))):
        m = re.match(r"state-(\d+)\.json$", f)
        if m and int(m.group(1)) != host:
            others |= set(R.load_state(int(m.group(1))).get("harness_titles", {}))
    for slug, tid in sorted(state.get("harness_titles", {}).items()):
        if slug in others:
            print(f"   {slug}: kept, another host's state holds it")
            continue
        st, _ = R.api("DELETE", f"/software/titles/{tid}/available_for_install?fleet_id={R.FID}")
        print(f"   {slug}: title {tid} removed" if st in (200, 204) else f"   {slug}: title delete returned {st}")


def delete(h):
    st, d = R.api("DELETE", f"/hosts/{h['id']}")
    print(f"host {h['id']} {h['hostname']}: " + ("deleted" if st in (200, 204) else f"delete returned {st} {d}"))
    return st in (200, 204)


def finish(host, hostname):
    h = ours(host, hostname)
    if not h:
        sys.exit(f"host {host} isn't the pool runner {hostname} in fleet {R.FID}: not touching it")
    drop_titles(host)
    if delete(h):
        try:
            os.remove(R.state_path(host))  # never saved: the next run is a new host ID
        except OSError:
            pass


def sweep(hours=7):
    """Pool hosts not seen for `hours` (longer than any job): a job killed before its finish step."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    for h in pool_hosts():
        seen = datetime.datetime.fromisoformat(h["seen_time"].replace("Z", "+00:00"))
        if seen < cutoff:
            drop_titles(h["id"])
            delete(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["wait", "finish", "sweep"])
    ap.add_argument("--host", type=int)
    ap.add_argument("--hostname")
    a = ap.parse_args()
    if a.cmd == "wait":
        wait(a.hostname)
    elif a.cmd == "finish":
        finish(a.host, a.hostname)
    else:
        sweep()


if __name__ == "__main__":
    main()
