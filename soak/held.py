"""Daily digest of soak items held longer than held.days, run in the watch job's loop.

Two kinds of hold sit unnoticed otherwise: quarantine entries (stuck.py blocked an app on a host after it hung the
queue; the entry stays until someone removes it) and leftovers (an app the harness installed but couldn't uninstall,
so its title can't be removed and the app keeps disk on the host). Quarantine entries carry their start; leftovers
don't, so this records when it first saw each one (results/state/held.json).

Once a day, from 07:00 local, it lists every item held longer than held.days as ::error:: lines and exits 1, so
ci/alert.sh opens or comments on the "Soak items held over 3 days" issue. Other checks only update held.json.

  python3 held.py [--now]   (--now: report regardless of the time and of today's digest)
"""
import datetime, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

STATE = os.path.join(R.WS, "results", "state")
PATH = os.path.join(STATE, "held.json")


def current():
    """{item: (kind, start or None, note)} for everything held right now."""
    items = {}
    q = os.path.join(STATE, "quarantine.json")
    for key, why in (json.load(open(q)) if os.path.exists(q) else {}).items():
        m = re.search(r"from (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", why)
        items[f"quarantine {key}"] = ("quarantine", m.group(1) if m else None, why.split(";")[0])
    for f in glob.glob(os.path.join(STATE, "state-*.json")):
        for key in json.load(open(f)).get("harness_installed", {}):
            items[f"leftover {key}"] = ("leftover", None, "installed by the harness, still on the host")
    return items


def check(now_flag=False):
    now = datetime.datetime.now()
    days = R.CFG.get("held", {}).get("days", 3)
    st = json.load(open(PATH)) if os.path.exists(PATH) else {}
    seen = st.setdefault("first_seen", {})
    items = current()
    for item, (kind, start, _) in items.items():
        seen.setdefault(item, start or now.isoformat(timespec="seconds"))
    for item in [i for i in seen if i not in items]:
        del seen[item]
    due = now_flag or (now.hour >= 7 and st.get("last_digest") != now.date().isoformat())
    old = sorted((i for i in items if (now - datetime.datetime.fromisoformat(seen[i])).days >= days),
                 key=lambda i: seen[i])
    print(f"{len(items)} held, {len(old)} for {days}+ days" + ("" if due else "; digest not due"))
    if due:
        st["last_digest"] = now.date().isoformat()
    os.makedirs(STATE, exist_ok=True)
    json.dump(st, open(PATH, "w"), indent=1)
    if not (due and old):
        return True
    for i in old:
        age = (now - datetime.datetime.fromisoformat(seen[i])).days
        print(f"::error::{i}: held {age} days (since {seen[i][:10]}): {items[i][2]}")
    return False


if __name__ == "__main__":
    sys.exit(0 if check("--now" in sys.argv) else 1)
