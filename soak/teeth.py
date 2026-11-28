"""Seeded failures: prove the cycle's host checks still see reality (fma-soak plan, phase 1).

falsesuccess.teeth() proves the false-success probe still catches a known-bad script. The lifecycle cycle has no
such proof: every verdict it gives ("installed", "uninstalled", "patched") comes from runner.present() running the
catalog's exists / patched query on the host. A query path that always answered "no rows" would turn every
uninstall into a pass, and the digest would read as clean.

Once a day on the platform's primary host, this plants a fake app (a bundle on macOS, an Uninstall registry key on
Windows; never a real installer) and asks runner.present() the catalog-shaped queries, in both directions:

  absent          nothing planted                 -> exists must be False  (an install that put nothing on disk)
  leftover        fake app 1.0.0 planted          -> exists must be True   (an uninstall that left the app)
  outdated        patched query for 2.0.0         -> must be False         (an old copy reported as patched)
  current         patched query for 0.5.0         -> must be True          (the patched query can pass at all)
  removed         fake app removed again          -> exists must be False

Any other answer opens a harness finding ("soak lost its teeth"), which the digest lists like the false-success
one. This checks the host-side half of each verdict (live query + osqueryd fallback); Fleet's own install status
path is covered by the false-success probe and the L1 suite.

  python3 teeth.py --platform darwin      once a day (pass.sh); --force to run again today
"""
import argparse, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R  # noqa: E402

OUT = os.path.join(R.WS, "results", "scenarios")
BUNDLE_ID, NAME, PUBLISHER, KEY = "com.fleetdm.fma-soak.teeth", "FMA Soak Teeth", "fma-soak", "FMASoakTeeth"
APP = f"/Applications/{NAME}.app"
UNINSTALL = rf"HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{KEY}"

PLANT = {
    "darwin": f"""set -e
mkdir -p "{APP}/Contents/MacOS"
cat > "{APP}/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
<key>CFBundleName</key><string>{NAME}</string>
<key>CFBundleShortVersionString</key><string>1.0.0</string>
<key>CFBundleVersion</key><string>1.0.0</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
echo planted""",
    "windows": f"""$k = '{UNINSTALL}'
New-Item -Path $k -Force | Out-Null
New-ItemProperty -Path $k -Name DisplayName -Value '{NAME}' -Force | Out-Null
New-ItemProperty -Path $k -Name DisplayVersion -Value '1.0.0' -Force | Out-Null
New-ItemProperty -Path $k -Name Publisher -Value '{PUBLISHER}' -Force | Out-Null
Write-Output planted""",
}
REMOVE = {
    "darwin": f'rm -rf "{APP}"; [ ! -e "{APP}" ] && echo removed',
    "windows": f"Remove-Item -Path '{UNINSTALL}' -Recurse -Force -ErrorAction SilentlyContinue\n"
               f"if (-not (Test-Path '{UNINSTALL}')) {{ Write-Output removed }}",
}


def queries(plat):
    """The catalog's exists / patched query shapes (ee/maintained-apps/outputs/*), aimed at the fake app."""
    if plat == "darwin":
        where = f"bundle_identifier = '{BUNDLE_ID}'"
        exists = f"SELECT 1 FROM apps WHERE {where};"
        patched = lambda v: (f"SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM apps WHERE {where} "
                             f"AND version_compare(bundle_short_version, '{v}') < 0);")
    else:
        where = f"name = '{NAME}' AND publisher = '{PUBLISHER}'"
        exists = f"SELECT 1 FROM programs WHERE {where};"
        patched = lambda v: (f"SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM programs WHERE {where} "
                             f"AND version_compare(version, '{v}') < 0);")
    return exists, patched


def script_ok(res, word):
    return res.get("exit_code") == 0 and word in (res.get("output") or "")


def check(plat, host, present=None, run_script=None):
    """[{name, want, got, ok}] for the five seeds; the fake app is removed again whatever happens."""
    present, run_script = present or R.present, run_script or R.run_script
    exists, patched = queries(plat)
    rows = []

    def ask(name, query, want):
        got = present(host, query)
        # no answer (host busy, script channel down) says nothing about the check: inconclusive, not lost teeth
        rows.append({"name": name, "want": want, "got": got, "ok": None if got is None else got is want})

    if not script_ok(run_script(host, REMOVE[plat], timeout=300), "removed"):  # a crashed earlier run's leftover
        return [{"name": "setup", "want": "removed", "got": "remove script failed", "ok": None}]
    ask("absent", exists, False)
    try:
        if not script_ok(run_script(host, PLANT[plat], timeout=300), "planted"):
            rows.append({"name": "plant", "want": "planted", "got": "plant script failed", "ok": None})
            return rows
        ask("leftover", exists, True)
        ask("outdated", patched("2.0.0"), False)
        ask("current", patched("0.5.0"), True)
    finally:
        removed = script_ok(run_script(host, REMOVE[plat], timeout=300), "removed")
    if removed:
        ask("removed", exists, False)
    else:
        rows.append({"name": "removed", "want": "removed", "got": "remove script failed: fake app left on the host",
                     "ok": None})
    return rows


def verdict(rows):
    """'ok', 'lost teeth: ...' (a check gave the wrong answer) or 'inconclusive: ...' (no answer, script failed)."""
    wrong = [r for r in rows if r["ok"] is False]
    if wrong:
        return "lost teeth: " + "; ".join(f"{r['name']} answered {r['got']}, expected {r['want']}" for r in wrong)
    unknown = [r for r in rows if r["ok"] is None]
    if unknown:
        return "inconclusive: " + "; ".join(f"{r['name']}: {r['got']}" for r in unknown)
    return "ok"


def run(plat, force=False):
    host = R.CFG["hosts"][plat][0]
    date = datetime.date.today().isoformat()
    day = os.path.join(OUT, f"{date}-teeth-{plat}.json")
    if os.path.exists(day) and not force:
        print(f"teeth {plat}: already ran today ({day})")
        return 0
    started = datetime.datetime.now().isoformat(timespec="seconds")
    rows = check(plat, host)
    v = verdict(rows)
    os.makedirs(OUT, exist_ok=True)
    json.dump({"date": date, "host": host, "build": R.build(), "started": started, "verdict": v, "checks": rows},
              open(day, "w"), indent=1)
    for r in rows:
        print(f"   {r['name']}: got {r['got']}, want {r['want']}")
    print(f"teeth {plat} on {host}: {v}")
    if v.startswith("lost teeth"):  # the digest shows it as a harness problem, like the false-success teeth
        R.finish({"slug": f"fma-soak-teeth/{plat}", "host": host, "kind": "teeth", "started": started,
                  "build": R.build(), "outcome": "findings",
                  "findings": [{"shape": "harness", "msg": f"soak lost its teeth on {host}: {v[len('lost teeth: '):]}"}]})
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["darwin", "windows"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.platform, a.force))
