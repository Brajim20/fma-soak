"""Low-disk watchdog, run beside hosts.py and stuck.py in the watch job every 30 min.

On 2026-11-05 host 631 ran down to 0.17 GB free (Affinity, OneDrive and Docker alone were 9.3 GB): fleetd could
barely run a script, its queue stopped moving and every cycle there was a spurious failure, for hours, unnoticed.

Reads each soak host's free space from Fleet's host details (gigs_disk_space_available), not a live query, so it
works when the host is too full to answer one. A host below disk.min_gb alerts once (exit 1 with ::error:: lines;
ci/alert.sh opens or comments on the "Soak host low on disk" issue), and once more when it's back above
disk.min_gb + disk.hysteresis_gb. Checks in between only print.

  python3 disk.py
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

PATH = os.path.join(R.WS, "results", "state", "disk.json")


def check():
    cfg = R.CFG.get("disk", {})
    low, back = cfg.get("min_gb", 5), cfg.get("min_gb", 5) + cfg.get("hysteresis_gb", 2)
    st = json.load(open(PATH)) if os.path.exists(PATH) else {}
    alerts = []
    for plat, hosts in R.CFG["hosts"].items():
        for host in hosts:
            code, d = R.api("GET", f"/hosts/{host}")
            h = d.get("host") if code == 200 else None
            free = h.get("gigs_disk_space_available") if h else None
            if free is None:
                print(f"{plat} host {host}: no disk figure from Fleet ({code}); not judged this check")
                continue
            was = st.get(str(host))
            if free < low and not was:
                st[str(host)] = {"since": h.get("seen_time"), "free_gb": free}
                alerts.append(f"{plat} host {host} ({h.get('hostname')}): {free} GB free, below {low} GB; installs "
                              f"there will start failing. Uninstall apps the soak doesn't need, or grow the disk.")
                print(f"{plat} host {host}: LOW, {free} GB free")
            elif was and free >= back:
                del st[str(host)]
                print(f"{plat} host {host}: disk OK again, {free} GB free (was {was['free_gb']} GB)")
            else:
                print(f"{plat} host {host}: {free} GB free" + (" (low, already alerted)" if was else ""))
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    json.dump(st, open(PATH, "w"), indent=1)
    for a in alerts:
        print(f"::error::{a}")
    return not alerts


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
