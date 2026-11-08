"""Broken-installer-store alert, run in the report job after every soak and pool job have saved their records.

A cycle whose stored installer is empty or truncated (runner.store_problem) records "environment: installer store"
instead of failing every install, and the sampler stops its pass after three in a row. This turns those records into
one alert (exit 1 with ::error:: lines; ci/alert.sh opens or comments on the "Soak installer store broken" issue),
and one more once a later run's cycles get through the check again. On 2026-11-06 qa.fleetdm.com's filesystem store
filled up and served empty installers for 20 h before anyone noticed (#57132).

  python3 store.py
"""
import glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

PATH = os.path.join(R.WS, "results", "state", "store.json")


def check(records, st):
    """(new state, lines to print, alert?) for the records started since the last check."""
    new = [r for r in records if r.get("started", "") > st.get("checked", "")]
    bad = [r for r in new if str(r.get("outcome", "")).startswith(R.STORE_BROKEN)]
    good = [r for r in new if r.get("title_id") and not str(r.get("outcome", "")).startswith((R.STORE_BROKEN, "skipped"))]
    out = {"checked": max([st.get("checked", "")] + [r["started"] for r in new]), "alerted": st.get("alerted", False)}
    if bad and not out["alerted"]:
        out["alerted"] = True
        lines = [f"::error::{len(bad)} cycle(s) found a broken stored installer on {R.CFG['instance']}; the sampler stops "
                 "a pass after 3 in a row, so the soak is mostly idle until the store is fixed"]
        lines += [f"  {r['slug']} @{r['host']} {r['started']} (title {r.get('title_id')}): {r['outcome'][len(R.STORE_BROKEN) + 2:]}"
                  for r in bad[:10]]
        return out, lines, True
    if out["alerted"] and good and not bad:
        out["alerted"] = False
        return out, [f"installer store OK again: {len(good)} cycle(s) since {st.get('checked')} got through the check"], False
    return out, [f"installer store: {len(bad)} broken, {len(good)} fine since {st.get('checked') or 'the start'}"], False


def main():
    recs = []
    for p in glob.glob(os.path.join(R.WS, "results", "soak", "*", "*.json")):
        try:
            recs.append(json.load(open(p)))
        except (ValueError, OSError):
            pass
    st = json.load(open(PATH)) if os.path.exists(PATH) else {}
    st, lines, alert = check(recs, st)
    print("\n".join(lines))
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    json.dump(st, open(PATH, "w"), indent=1)
    sys.exit(1 if alert else 0)


if __name__ == "__main__":
    main()
