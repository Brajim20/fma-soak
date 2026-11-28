"""Uninstall leftovers: what the install put on the host that the uninstall didn't take away (fma-soak plan, phase 2).

The cycle's uninstall check is the catalog's exists query: Fleet says uninstalled and the query no longer matches.
Nothing looked at the disk, so Herdr's staged update copy (/Applications/Herdr.app.new.noindex) only surfaced as a
false watchdog alarm (2026-11-26).

Three snapshots per cycle, each one read-only osquery live query (4-9 s on 636 and 635, 2026-11-28); nothing is
queued or run on the host, and nothing is written there. The snapshots stay in this process's memory:

  0  before the first install
  1  after the first install
  2  after the last uninstall  -> leftovers = in 1 but not in 0 (the install created it), and still in 2

Created-by-the-install-and-still-there filters out the host's own churn between snapshots (OS updates, other
agents), which a plain before/after diff would report. Places looked at:

  macOS    /Applications, ~/Applications, LaunchAgents/LaunchDaemons (system and per user), PrivilegedHelperTools,
           /Library/Extensions, system extensions, pkg receipts, Application Support / Preferences / Caches
           (system and per user)
  Windows  Program Files (both), ProgramData, AppData Local/Roaming (incl. Local\\Programs), Uninstall keys (HKLM
           both views, HKEY_USERS), services, scheduled tasks, HKLM Run values

Each item gets a kind; config leftovers.finding_kinds decides which are findings (shape "leftover"). The rest
(preferences, caches, app data, pkg receipts) go on the record only: apps keep user data by design, and Fleet's
generated uninstall scripts don't promise to remove it.

A cycle's retry starts from a host that still has the first run's leftovers, so its snapshot 0 already holds them
and they wouldn't count as created. carry() hands the first run's items to the retry, which reports any that are
still there, so a leftover reproduces instead of turning the result flaky.
"""
import os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R  # noqa: E402

CFG = R.CFG.get("leftovers", {})
FINDING_KINDS = set(CFG.get("finding_kinds", ["app", "launchd", "helper", "extension", "program_dir",
                                               "uninstall_key", "service", "task", "run_key"]))
MAX_ITEMS = CFG.get("max_items", 80)  # per kind, on the record
# the OS's own churn during a cycle, never the app's (2026-11-28 on 635: Windows MDM added an EnterpriseMgmt
# SessionRetry task mid-cycle; per-user services get a new suffix, "CDPUserSvc_6f65b", each sign-in)
IGNORE = [re.compile(p) for p in CFG.get("ignore", [r"^task:\\Microsoft\\Windows\\", r"^service:.+_[0-9a-f]{4,8}$"])]
CARRY = {}  # "slug@host" -> finding-kind items the first run of a retry pair left (unscrubbed)
SNAPS = {}  # "slug@host" -> {phase: set of items}, this cycle's snapshots (process memory only, never saved)
RAW = {}    # "slug@host" -> the last cycle's finding-kind leftovers, unscrubbed: the record's copy gets user names
            # scrubbed and wouldn't match the host's paths in the retry


def q(v):
    return "'" + v.replace("'", "''") + "'"


MAC_DIRS = ["/Applications", "/Library/LaunchAgents", "/Library/LaunchDaemons", "/Library/PrivilegedHelperTools",
            "/Library/Extensions", "/Library/Application Support", "/Library/Preferences", "/Library/Caches"]
MAC_USER = ["Applications", "Library/LaunchAgents", "Library/Application Support", "Library/Preferences",
            "Library/Caches"]
_UNI = r"\Microsoft\Windows\CurrentVersion\Uninstall"
_RUN = r"\Microsoft\Windows\CurrentVersion\Run"
# osquery's file table expands % within one path segment: 'C:\Program Files%\%' lists both Program Files folders
QUERY = {
    "darwin": " UNION ".join([
        "SELECT path AS item FROM file WHERE directory IN (%s)" % ", ".join(map(q, MAC_DIRS)),
        "SELECT path AS item FROM file WHERE " + " OR ".join(f"path LIKE {q('/Users/%/' + d + '/%')}" for d in MAC_USER),
        "SELECT 'receipt:' || package_id AS item FROM package_receipts",
        "SELECT 'sysext:' || identifier AS item FROM system_extensions",
    ]) + ";",
    "windows": " UNION ".join([
        "SELECT 'dir:' || path AS item FROM file WHERE path LIKE " + q("C:\\Program Files%\\%")
        + " OR path LIKE " + q("C:\\ProgramData\\%"),
        "SELECT 'dir:' || path AS item FROM file WHERE " + " OR ".join(
            "path LIKE " + q("C:\\Users\\%\\AppData\\" + d + "\\%") for d in ("Local\\Programs", "Local", "Roaming")),
        # the Uninstall keys' subkeys (path), not the keys of their values: a key with no values has none
        "SELECT 'key:' || path AS item FROM registry WHERE key IN (" + q("HKEY_LOCAL_MACHINE\\SOFTWARE" + _UNI)
        + ", " + q("HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node" + _UNI) + ") OR key LIKE " + q("HKEY_USERS\\%\\Software" + _UNI),
        "SELECT 'service:' || name AS item FROM services",
        "SELECT 'task:' || path AS item FROM scheduled_tasks",
        "SELECT 'run:' || key || '\\' || name AS item FROM registry WHERE key IN ("
        + q("HKEY_LOCAL_MACHINE\\SOFTWARE" + _RUN) + ", " + q("HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node" + _RUN) + ")",
    ]) + ";",
}


def kind(plat, item):
    """What a snapshot item is: app, launchd, helper, extension, receipt, data (macOS); program_dir, data,
    uninstall_key, service, task, run_key (Windows)."""
    if plat == "darwin":
        if item.startswith("receipt:"):
            return "receipt"
        if item.startswith("sysext:"):
            return "extension"
        if re.match(r"(/Users/[^/]+)?/Applications/", item):
            return "app"
        if "/LaunchAgents/" in item or "/LaunchDaemons/" in item:
            return "launchd"
        if item.startswith("/Library/PrivilegedHelperTools/"):
            return "helper"
        if item.startswith("/Library/Extensions/"):
            return "extension"
        return "data"  # Application Support, Preferences, Caches
    pre, _, rest = item.partition(":")
    if pre == "key":
        return "uninstall_key"
    if pre in ("service", "task"):
        return pre
    if pre == "run":
        return "run_key"
    low = rest.lower()
    if re.match(r"c:\\program files( \(x86\))?\\", low) or "\\appdata\\local\\programs\\" in low:
        return "program_dir"
    return "data"  # ProgramData, AppData Local/Roaming


def snapshot(host, plat, tries=3):
    """The set of items, or None when the host didn't answer (busy right after an install: retried)."""
    for i in range(tries):
        st, d = R.api("POST", f"/hosts/{host}/query", {"query": QUERY[plat]}, timeout=180)
        if st == 200 and not d.get("error") and d.get("rows"):
            # a folder's path ends in a separator on Windows ("C:\Program Files\X\"): one spelling per item
            return {r["item"].rstrip("/\\") for r in d["rows"] if r.get("item")}
        if i < tries - 1:
            time.sleep(20)
    return None


def enabled(rec):
    return CFG.get("enabled", True) and not rec.get("coexist_per_user")


def take(host, plat, rec, phase, snap=snapshot):
    """Snapshot `phase` (0, 1, 2); on no answer the cycle's leftover check is off (recorded, never a finding)."""
    lo = rec.get("leftovers")
    if not lo or not lo.get("ok"):
        return False
    t0 = time.time()
    s = snap(host, plat)
    lo.setdefault("seconds", 0)
    lo["seconds"] += round(time.time() - t0)
    if s is None:
        lo.update(ok=False, error=f"snapshot {phase}: host didn't answer the live query")
        SNAPS.pop(f"{rec['slug']}@{host}", None)
        return False
    SNAPS.setdefault(f"{rec['slug']}@{host}", {})[phase] = s
    return True


def start(host, plat, rec, snap=snapshot):
    """Snapshot 0, right before the first install is requested."""
    if not enabled(rec):
        return
    SNAPS.pop(f"{rec['slug']}@{host}", None)
    rec["leftovers"] = {"ok": True}
    take(host, plat, rec, 0, snap)


def after_install(host, plat, rec, snap=snapshot):
    """Snapshot 1, right after the first install (before its uninstall is requested)."""
    take(host, plat, rec, 1, snap)


def finish(host, plat, rec, find, app_gone, snap=snapshot):
    """Snapshot 2 and the diff. Findings only when the uninstall check already says the app is gone (app_gone):
    an app still on the host is its own S3 / uninstall finding."""
    key = f"{rec['slug']}@{host}"
    lo = rec.get("leftovers")
    if not lo or not lo.get("ok") or 1 not in SNAPS.get(key, {}):
        SNAPS.pop(key, None)
        return
    if not take(host, plat, rec, 2, snap):
        return
    s = SNAPS.pop(key)
    created = s[1] - s[0]
    left = sorted(it for it in (created | set(CARRY.pop(key, ()))) & s[2] if not any(p.search(it) for p in IGNORE))
    by_kind = {}
    for it in left:
        by_kind.setdefault(kind(plat, it), []).append(it)
    lo.update(created=len(created), left=len(left),
              items={k: R.scrub_all(v[:MAX_ITEMS]) for k, v in sorted(by_kind.items())})
    bad = [it for k, v in sorted(by_kind.items()) if k in FINDING_KINDS for it in v]
    RAW[key] = bad
    if bad and app_gone:
        shown = ", ".join(R.scrub(x) for x in bad[:3]) + (f" and {len(bad) - 3} more" if len(bad) > 3 else "")
        find("leftover", f"the uninstall left {len(bad)} item{'s' if len(bad) != 1 else ''} the install created: {shown}")


def carry(first, host):
    """Before a retry: hand the first run's finding-kind leftovers (unscrubbed) to the second run."""
    key = f"{first['slug']}@{host}"
    items = RAW.pop(key, [])
    if items:
        CARRY[key] = items
