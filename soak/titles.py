#!/usr/bin/env python3
"""Remove the soak's titles from the soak fleet once a run is over, run in the report job after every soak and pool job.

Every cycle adds its app's title if the fleet doesn't have it, and each stored installer takes disk on the instance.
Per-cycle removal (sample.py remove_if_added, pool.py drop_titles) relies on host state and misses titles that
crosshost, recheck, the update path and scenarios add, titles a cycle found already there, and state a job lost.
On qa.fleetdm.com (filesystem installer store, no S3) the leftovers filled the disk and later adds stored empty or
truncated installers (#57132, 2026-11-06). Fleet's hourly cleanup deletes a stored installer once no title uses it.

Removes every Fleet-maintained app title in the fleet except:
  - canaries and watcher residents (config.json)
  - title_cleanup.keep_title_ids: titles that were in the fleet before the soak (never the harness's to remove)
  - an app patch_closed.py holds across passes (runner.busy_slugs)
  - an app the harness installed and couldn't uninstall from a host (harness_installed in any state file): the
    title is how runner.py --cleanup removes it later
  - a title with an install or uninstall queued on any host in the fleet
Titles that aren't Fleet-maintained apps (custom packages) are never touched.

  python3 titles.py sweep [--dry-run]
"""
import argparse, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

STATE = os.path.join(R.WS, "results", "state")


def kept_slugs():
    """{slug: why} for the apps whose titles stay."""
    keep = {}
    for xs in R.CFG.get("canaries", {}).values():
        keep.update({s: "canary" for s in xs})
    for xs in R.CFG.get("watcher", {}).get("residents", {}).values():
        keep.update({s: "watcher resident" for s in xs})
    keep.update({s: "held by patch_closed" for s in R.busy_slugs()})
    for f in glob.glob(os.path.join(STATE, "state-*.json")):
        for key in json.load(open(f)).get("harness_installed", {}):
            keep.setdefault(R.app_slug(key), f"still on host {key.split('@')[-1]}")
    return keep


def queued_titles():
    """Title IDs and names with upcoming activities on any host in the fleet."""
    out = set()
    st, d = R.api("GET", f"/hosts?fleet_id={R.FID}&per_page=500")
    if st != 200:
        raise SystemExit(f"listing fleet {R.FID} hosts returned {st}: not removing anything")
    for h in d.get("hosts", []):
        st, u = R.api("GET", f"/hosts/{h['id']}/activities/upcoming?per_page=200")
        if st != 200:
            raise SystemExit(f"host {h['id']} upcoming activities returned {st}: not removing anything")
        for a in u.get("activities") or []:
            det = a.get("details") or {}
            out |= {det.get("software_title_id"), det.get("software_title")}
    return out - {None}


def forget(removed):
    """Drop removed titles from every host's harness_titles so nothing tries to delete them again."""
    for f in glob.glob(os.path.join(STATE, "state-*.json")):
        s = json.load(open(f))
        titles = s.get("harness_titles", {})
        gone = [slug for slug, tid in titles.items() if tid in removed]
        if gone:
            for slug in gone:
                titles.pop(slug)
            json.dump(s, open(f, "w"), indent=1)


def sweep(dry):
    keep_ids = set(R.CFG.get("title_cleanup", {}).get("keep_title_ids", []))
    keep = kept_slugs()
    busy = queued_titles()
    st, d = R.api("GET", f"/software/fleet_maintained_apps?fleet_id={R.FID}&per_page=5000")
    if st != 200:
        raise SystemExit(f"listing the FMA catalog returned {st}: not removing anything")
    removed, kept = set(), {}
    for a in d.get("fleet_maintained_apps", []):
        tid = a.get("software_title_id")
        if not tid:
            continue
        why = ("in the fleet before the soak" if tid in keep_ids else keep.get(a["slug"])
               or ("queued on a host" if tid in busy or a["name"] in busy else None))
        if why:
            kept[why] = kept.get(why, 0) + 1
            continue
        if dry:
            print(f"would remove {a['slug']} (title {tid})")
            continue
        st, body = R.api("DELETE", f"/software/titles/{tid}/available_for_install?fleet_id={R.FID}")
        if st in (200, 204):
            removed.add(tid)
        else:
            print(f"{a['slug']}: title {tid} delete returned {st} {body.get('message') if isinstance(body, dict) else ''}")
    if removed:
        forget(removed)
    print(f"{'dry run: ' if dry else ''}removed {len(removed)} titles from fleet {R.FID}; kept {sum(kept.values())}: "
          + ", ".join(f"{n} {why}" for why, n in sorted(kept.items())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sweep(a.dry_run)


if __name__ == "__main__":
    main()
