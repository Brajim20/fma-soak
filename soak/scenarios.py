"""Task 4.4: Windows second-explorer.exe scenario (#51127), once a day from the Windows pass.

#51127: per-user Windows FMA install scripts resolved the signed-in user from explorer.exe's owner; with two
explorer.exe processes the user name became "user user", Register-ScheduledTask failed silently and the install
reported "Timed-out waiting for scheduled task state". Fixed in the catalog by #56608 (2026-10-27).

Each run:
1. Probe (teeth): the pre-fix Figma install script, run on the host with a harmless .cmd standing in for the
   installer — once with one explorer.exe (must succeed: proves the probe works) and once with a second
   explorer.exe (must time out: proves the scenario still triggers the bug). Self-contained; cleans up.
2. With a second explorer.exe running, real Fleet lifecycle cycles for today's apps from the affected list
   (rotating by date). They must pass, like any cycle.
3. The extra explorer.exe is closed whatever happened.

The second explorer.exe is `explorer.exe /separate,...` started as the signed-in user through a one-shot
scheduled task; closing it kills every explorer.exe of that user except the oldest (the shell).

  python3 scenarios.py explorer2             # what pass.sh runs (skips if already done today)
  python3 scenarios.py explorer2 --probe-only
"""
import argparse, base64, datetime, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R
import sample as S

OUT = os.path.join(R.WS, "results", "scenarios")
CFG = R.CFG.get("scenarios", {}).get("explorer2", {})

USER = r'''
$owners = @(Get-CimInstance Win32_Process -Filter 'name = "explorer.exe"' | ForEach-Object {
  $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
  [pscustomobject]@{ Pid = $_.ProcessId; User = $o.User; Domain = $o.Domain; Created = $_.CreationDate } })
$shellUser = ($owners | Sort-Object Created | Select-Object -First 1).User
'''

START_EXTRA = USER + r'''
if (-not $shellUser) { Write-Output "NO_USER"; exit 3 }
$t = "fleet-qa-explorer2"
$a = New-ScheduledTaskAction -Execute "explorer.exe" -Argument "/separate,C:\Users\Public"
$p = New-ScheduledTaskPrincipal -UserId $shellUser -LogonType Interactive
Register-ScheduledTask -TaskName $t -Action $a -Principal $p -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskName $t
Start-Sleep -Seconds 8
Unregister-ScheduledTask -TaskName $t -Confirm:$false
$n = @(Get-CimInstance Win32_Process -Filter 'name = "explorer.exe"').Count
Write-Output "EXPLORER_COUNT=$n"
'''

STOP_EXTRA = USER + r'''
$extra = @($owners | Where-Object { $_.User -eq $shellUser } | Sort-Object Created | Select-Object -Skip 1)
foreach ($e in $extra) { Stop-Process -Id $e.Pid -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$n = @(Get-CimInstance Win32_Process -Filter 'name = "explorer.exe"').Count
Write-Output "STOPPED=$($extra.Count) EXPLORER_COUNT=$n"
'''


def probe_script(old_b64):
    return r'''
$ErrorActionPreference = "Continue"
$dir = Join-Path $env:TEMP "fleet-qa-51127"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$old = Join-Path $dir "figma-install-prefix.ps1"
[IO.File]::WriteAllText($old, [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("''' + old_b64 + r'''")))
$stub = Join-Path $dir "Figma-probe.cmd"
Set-Content -Path $stub -Value "@ping -n 12 127.0.0.1 >nul" -Encoding ascii
function Run-Old($label) {
  $env:INSTALLER_PATH = $stub
  $t0 = Get-Date
  $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $old 2>&1 | Out-String
  $code = $LASTEXITCODE
  $secs = [int]((Get-Date) - $t0).TotalSeconds
  Unregister-ScheduledTask -TaskName "fleet-install-Figma-probe.cmd" -Confirm:$false -ErrorAction SilentlyContinue
  $tail = ($out -split "`n" | Where-Object { $_ -match "Error|Timed-out|No MSFT_ScheduledTask|mapping|ScheduledTask is" } | Select-Object -First 3) -join " / "
  Write-Output "RUN $label exit=$code secs=$secs :: $tail"
}
''' + USER + r'''
$n1 = @($owners).Count
Write-Output "BEFORE explorer_count=$n1 user=$shellUser"
Run-Old "one-explorer"
''' + START_EXTRA + r'''
Run-Old "two-explorers"
''' + STOP_EXTRA + r'''
Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
'''


def run_script(host, body, timeout=900):
    st, d = R.api("POST", "/scripts/run", {"host_id": host, "script_contents": body})
    if st >= 300:
        return {"error": f"{st} {d.get('message')}"}
    eid = d.get("execution_id")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(15)
        st, r = R.api("GET", f"/scripts/results/{eid}")
        if r.get("exit_code") is not None:
            return {"execution_id": eid, "exit_code": r["exit_code"], "output": R.scrub(r.get("output", ""))}
    return {"execution_id": eid, "error": "no result in time"}


def pre_fix_script():
    ref = CFG.get("pre_fix_ref", "0505918dc5c7daaad71c1452e881643e3c8d81ca^1")
    j = json.loads(subprocess.run(["git", "-C", R.REPO, "show", f"{ref}:ee/maintained-apps/outputs/figma/windows.json"],
                                  capture_output=True, text=True, check=True).stdout)
    return j["refs"][j["versions"][0]["install_script_ref"]]


def probe(host):
    body = probe_script(base64.b64encode(pre_fix_script().encode()).decode())
    res = run_script(host, body)
    out = res.get("output", "")
    one = next((l for l in out.splitlines() if l.startswith("RUN one-explorer")), "")
    two = next((l for l in out.splitlines() if l.startswith("RUN two-explorers")), "")
    counts = [l for l in out.splitlines() if "EXPLORER_COUNT" in l]
    res["verdict"] = (
        "teeth: pre-fix script works with one explorer.exe and fails with two" if "exit=0" in one and "exit=1" in two
        else "probe inconclusive")
    res.update(one=one, two=two, counts=counts)
    return res


def explorer2(probe_only=False):
    os.makedirs(OUT, exist_ok=True)
    host = R.CFG["hosts"]["windows"][0]
    date = datetime.date.today().isoformat()
    path = os.path.join(OUT, f"{date}-explorer2.json")
    if os.path.exists(path) and not probe_only:
        print(f"explorer2: already ran today ({path})")
        return
    rec = {"scenario": "second explorer.exe (#51127)", "date": date, "host": host, "build": R.build(), "apps": []}
    rec["probe"] = probe(host)
    print(f"explorer2 probe: {rec['probe']['verdict']}\n   {rec['probe'].get('one')}\n   {rec['probe'].get('two')}")
    if not probe_only:
        apps = CFG.get("apps", ["figma/windows", "airtable/windows", "brave-browser/windows",
                                "grammarly-desktop/windows", "postman/windows"])
        k = CFG.get("per_day", 2)
        start = (datetime.date.today().toordinal() * k) % len(apps)
        today = [apps[(start + i) % len(apps)] for i in range(k)]
        started = run_script(host, START_EXTRA, timeout=300)
        rec["extra_explorer"] = started.get("output", started.get("error", "")).strip()
        try:
            for slug in today:
                r = R.run(R.cycle, slug, host)
                r["scenario"] = rec["scenario"]
                if r.get("record"):
                    json.dump(r, open(r["record"], "w"), indent=1)
                cleanup = S.remove_if_added(slug, host, r) if slug not in R.CFG["canaries"]["windows"] else "kept: canary"
                rec["apps"].append({"slug": slug, "outcome": r.get("outcome"), "record": r.get("record"), "cleanup": cleanup})
                print(f"   {slug} with two explorer.exe: {r.get('outcome')}")
        finally:
            stopped = run_script(host, STOP_EXTRA, timeout=300)
            rec["closed_extra"] = stopped.get("output", stopped.get("error", "")).strip()
    json.dump(rec, open(path if not probe_only else path.replace(".json", "-probe.json"), "w"), indent=1)


def perms55093():
    """Task 4.5 teeth: install Firefox on the macOS host with the pre-#55203 install script (what Fleet shipped
    before the #55093 fix), then run the harness's own context check on it. It must flag the executable mode.
    Skips if Firefox is already on the host; removes only the copy it installed."""
    host = R.CFG["hosts"]["darwin"][0]
    pr_merge = subprocess.run(["gh", "pr", "view", "55203", "-R", "fleetdm/fleet", "--json", "mergeCommit", "-q",
                               ".mergeCommit.oid"], capture_output=True, text=True).stdout.strip()
    j = json.loads(subprocess.run(["git", "-C", R.REPO, "show", f"{pr_merge}^1:ee/maintained-apps/outputs/firefox/darwin.json"],
                                  capture_output=True, text=True, check=True).stdout)
    v = j["versions"][0]
    old = j["refs"][v["install_script_ref"]]
    url = R.catalog_manifest("firefox/darwin")["versions"][0]["installer_url"]
    b64 = base64.b64encode(old.encode()).decode()
    install = f"""#!/bin/bash
D=/private/tmp/fleet-qa-55093
if [ -d /Applications/Firefox.app ]; then echo "SKIP: Firefox already installed"; exit 0; fi
rm -rf "$D"; mkdir -p "$D/dl"
curl -sSL -o "$D/dl/Firefox.dmg" '{url}' || {{ echo "download failed"; exit 2; }}
echo '{b64}' | base64 -D > "$D/old-install.sh"
INSTALLER_PATH="$D/dl/Firefox.dmg" bash "$D/old-install.sh"; echo "OLD_SCRIPT_EXIT=$?"
touch "$D/installed-by-probe"
stat -f "STAT %Su:%Sg %Sp %N" /Applications/Firefox.app /Applications/Firefox.app/Contents/MacOS/firefox
"""
    print(f"pre-fix script from {pr_merge[:10]}^1 ({len(install)} chars)")
    r1 = run_script(host, install)
    print(r1.get("output", r1)[-800:] if isinstance(r1.get("output"), str) else r1)
    if "SKIP" in r1.get("output", ""):
        return
    ctx = R.context(host, R.catalog_manifest("firefox/darwin")["versions"][0]["queries"]["exists"])
    found = []
    R.context_findings(ctx, lambda shape, msg: found.append((shape, msg)), "pre-fix install", "firefox/darwin")
    print("harness context:", ctx)
    print("harness findings:", found or "NONE (no teeth)")
    r2 = run_script(host, """#!/bin/bash
D=/private/tmp/fleet-qa-55093
if [ -f "$D/installed-by-probe" ]; then rm -rf /Applications/Firefox.app; echo removed; fi
rm -rf "$D"
""")
    print("cleanup:", r2.get("output", r2))
    os.makedirs(OUT, exist_ok=True)
    json.dump({"date": datetime.date.today().isoformat(), "build": R.build(), "pre_fix_ref": f"{pr_merge}^1",
               "install_output": r1.get("output", ""), "context": ctx, "findings": found, "cleanup": r2.get("output")},
              open(os.path.join(OUT, f"{datetime.date.today().isoformat()}-perms55093.json"), "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=["explorer2", "perms55093"])
    ap.add_argument("--probe-only", action="store_true")
    a = ap.parse_args()
    explorer2(a.probe_only) if a.scenario == "explorer2" else perms55093()


if __name__ == "__main__":
    main()
