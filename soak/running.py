"""S6: the app is open while Fleet installs or updates it (gap 1 from the escape review, #55961).

The escapes this guards (~14, nearly all customer-reported): #54170 and #55338 (replaced under a running app, which
kept running from deleted files while Fleet said installed), #56809 (quit and never reopened), #48638 #48639 #48225
#49491 (opened by an update when it hadn't been open), #30239 #32227 #32634 (unusable after an update).

What the install scripts are meant to do, from ee/maintained-apps/ingesters/homebrew/scripts.go
(quit_and_track_application / relaunch_application): if the app is running, quit it; if it won't quit, stop
(`|| exit 1`) and leave it alone; after installing, reopen it only if it was running. Windows has no shared
quit/relaunch step (per-app scripts stop processes; browsers update in place), so on Windows only the outcome is
checked: the install over the open app succeeds, the app isn't opened by an update, and it still starts after.

Two places use it:
- cycle: after the second install, open the app and install it again over the open copy (every app in the walk)
- update path: on alternate days per app, open the older version before upgrading; on the other days check the
  upgrade didn't open it

A check that needs the app to start only counts when the same record already saw it start, so an app that simply
can't be opened here (an agent, a first-run dialog, no launchable .exe) is a note, not a finding.
"""
import datetime, re, shlex, time, zlib

import runner as R

SETTLE = 20  # seconds an app gets to show up after it's opened


def mode_for(slug):
    """'open' or 'closed' for today's update path of this app; config running_app.update_path."""
    want = R.CFG.get("running_app", {}).get("update_path", "alternate")
    if want in ("open", "closed", "off"):
        return want
    return "open" if (datetime.date.today().toordinal() + zlib.crc32(slug.encode())) % 2 == 0 else "closed"


def in_cycle(slug):
    """Whether this cycle installs over the open app: always for canaries (most of these escapes were canary apps:
    Chrome, Firefox, Zoom, Edge), else for running_app.cycle_share of the sample (it adds ~3-4 min to a ~13 min
    cycle, so the walk covers the catalog more slowly). Deterministic per app and day."""
    cfg = R.CFG.get("running_app", {})
    if not cfg.get("cycle", True):
        return False
    if any(slug in xs for xs in R.CFG.get("canaries", {}).values()):
        return True
    share = cfg.get("cycle_share", 0.5)
    return (zlib.crc32(f"{slug}{datetime.date.today()}".encode()) % 100) < share * 100


def target(plat, ctx):
    """What to open and how to find it, from runner.context(); None if the context has nothing to go on."""
    ctx = ctx or {}
    if plat == "darwin" and ctx.get("path") and ctx.get("exe"):
        return {"plat": plat, "app": ctx["path"], "exe": ctx["exe"], "bundle_id": ctx.get("bundle_id")}
    if plat == "windows" and ctx.get("names"):
        return {"plat": plat, "names": ctx["names"], "locations": [l for l in ctx.get("install_locations", []) if l]}
    return None


def ps_list(xs):
    return "@(" + ", ".join("'" + x.replace("'", "''") + "'" for x in xs) + ")"


MAC_USER = '''u=$(stat -f%Su /dev/console)
if [ -z "$u" ] || [ "$u" = root ] || [ "$u" = loginwindow ]; then echo NO_USER; exit 3; fi
uid=$(id -u "$u")
# the path goes to awk through the environment: in its arguments, awk would match (and kill) itself
pids() { ps -axo pid=,command= | P="$APP/Contents/MacOS/" awk 'index($0, ENVIRON["P"]) > 0 {print $1}' | tr '\\n' ' '; }
'''

WIN_USER = r'''
$owners = @(Get-CimInstance Win32_Process -Filter 'name = "explorer.exe"' | ForEach-Object {
  $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
  [pscustomobject]@{ User = $o.User; Created = $_.CreationDate } })
$shellUser = ($owners | Sort-Object Created | Select-Object -First 1).User
'''

# The launchable .exe: the uninstall entry's DisplayIcon (machine and per-user hives), else the only plausible
# .exe at the top of the install location. MSIX apps and anything without one print NO_TARGET.
WIN_FIND = r'''
$exe = $null
$roots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')
New-PSDrive -Name HKU -PSProvider Registry -Root HKEY_USERS -ErrorAction SilentlyContinue | Out-Null
$roots += @(Get-ChildItem HKU:\ -ErrorAction SilentlyContinue | ForEach-Object { "HKU:\$($_.PSChildName)\Software\Microsoft\Windows\CurrentVersion\Uninstall" })
foreach ($r in $roots) {
  if (-not (Test-Path $r)) { continue }
  foreach ($k in Get-ChildItem $r -ErrorAction SilentlyContinue) {
    $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
    if ($names -contains $p.DisplayName -and $p.DisplayIcon) {
      $c = ($p.DisplayIcon -split ',')[0].Trim('"', ' ')
      if ($c -like '*.exe' -and (Test-Path $c) -and ((Split-Path $c -Leaf) -notmatch 'unins|setup|update')) { $exe = $c; break }
    }
  }
  if ($exe) { break }
}
if (-not $exe) {
  foreach ($l in $locs) {
    if (-not (Test-Path $l)) { continue }
    $c = @(Get-ChildItem $l -Filter *.exe -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'unins|setup|update|crash|helper|elevat' })
    if ($c.Count -eq 1) { $exe = $c[0].FullName; break }
  }
}
'''


def _open_script(t):
    if t["plat"] == "darwin":
        return (f"APP={shlex.quote(t['app'])}\n" + MAC_USER +
                f'/bin/launchctl asuser "$uid" sudo -u "$u" open -a "$APP"; echo "OPEN_EXIT=$?"\n'
                f'for i in $(seq 1 {SETTLE // 2}); do sleep 2; [ -n "$(pids)" ] && break; done\n'
                'echo "PIDS=$(pids)"\n')
    return (f"$names = {ps_list(t['names'])}\n$locs = {ps_list(t['locations'])}\n" + WIN_FIND +
            'if (-not $exe) { Write-Output "NO_TARGET"; exit 4 }\nWrite-Output "TARGET=$exe"\n' + WIN_USER +
            r'''if (-not $shellUser) { Write-Output "NO_USER"; exit 3 }
$t = "fleet-qa-open-app"
$a = New-ScheduledTaskAction -Execute $exe
$p = New-ScheduledTaskPrincipal -UserId $shellUser -LogonType Interactive
Register-ScheduledTask -TaskName $t -Action $a -Principal $p -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskName $t
''' + f"Start-Sleep -Seconds {SETTLE}\n" + r'''Unregister-ScheduledTask -TaskName $t -Confirm:$false
$ids = @(Get-Process | Where-Object { $_.Path -eq $exe } | ForEach-Object { $_.Id })
Write-Output "PIDS=$($ids -join ' ')"
''')


def _close_script(t):
    if t["plat"] == "darwin":
        bid = shlex.quote(t.get("bundle_id") or "")
        return (f"APP={shlex.quote(t['app'])}\nBID={bid}\n" + MAC_USER +
                '[ -n "$BID" ] && /bin/launchctl asuser "$uid" sudo -u "$u" osascript -e "tell application id \\"$BID\\" to quit" >/dev/null 2>&1\n'
                'sleep 5; p=$(pids); [ -n "$p" ] && kill $p; sleep 2; p=$(pids); [ -n "$p" ] && kill -9 $p\n'
                # a copy left running from a moved-aside bundle (#54170) has the same executable name under *.app.bkp
                f'b=$(ps -axo pid=,command= | P={shlex.quote(t["app"].rsplit("/", 1)[-1] + ".bkp/Contents/MacOS/")} '
                "awk 'index($0, ENVIRON[\"P\"]) > 0 {print $1}'); [ -n \"$b\" ] && kill -9 $b\n"
                'echo "LEFT=$(pids)"\n')
    exe = t.get("exe", "").replace("'", "''")
    return (f"$exe = '{exe}'\n"
            r'''$ps = @(Get-Process | Where-Object { $_.Path -eq $exe })
foreach ($x in $ps) { $x.CloseMainWindow() | Out-Null }
Start-Sleep -Seconds 5
Get-Process | Where-Object { $_.Path -eq $exe } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Output "LEFT=$(@(Get-Process | Where-Object { $_.Path -eq $exe }).Count)"
''')


def find_exe(host, t):
    """Windows: fill in t["exe"] without opening the app (the update path's closed mode needs it to look)."""
    if t["plat"] != "windows" or t.get("exe"):
        return
    body = f"$names = {ps_list(t['names'])}\n$locs = {ps_list(t['locations'])}\n" + WIN_FIND + \
        'if ($exe) { Write-Output "TARGET=$exe" } else { Write-Output "NO_TARGET" }\n'
    m = re.search(r"TARGET=(.+)", R.run_script(host, body, timeout=300, raw=True).get("raw_output", ""))
    if m:
        t["exe"] = m.group(1).strip()


def open_app(host, t, rec, label):
    """Open the app as the signed-in user. True if it's running afterwards (t gets the Windows exe)."""
    r = R.run_script(host, _open_script(t), timeout=300, raw=True)
    out = r.get("output", "") or r.get("error", "")
    # the exe from the unscrubbed output: the scrubbed C:\Users\<user>\... path matched no process, so per-user
    # installs (Granola, Termius) read as "doesn't stay open" and close_app never closed them
    m = re.search(r"TARGET=(.+)", r.get("raw_output", ""))
    if m:
        t["exe"] = m.group(1).strip()
    pids = (re.search(r"PIDS=(.*)", out) or [None, ""])[1].split()
    t["pids"] = sorted(set(t.get("pids", [])) | set(pids))  # processes() follows them if the install renames the exe
    rec[f"{label}_open"] = {"exit": r.get("exit_code"), "pids": pids,
                            "note": next((w for w in ("NO_TARGET", "NO_USER") if w in out), None) or r.get("error")}
    return bool(pids)


def close_app(host, t, rec, label):
    if t["plat"] == "windows" and not t.get("exe"):
        return
    r = R.run_script(host, _close_script(t), timeout=300)
    left = (re.search(r"LEFT=(.*)", r.get("output", "")) or [None, r.get("error")])[1]
    rec[f"{label}_close"] = left.strip() if isinstance(left, str) else left  # Windows output ends lines with \r


def processes(host, t):
    """The app's main processes, from osquery: [{pid, path, on_disk}], or None if the host didn't answer.
    macOS: the bundle's executable; the same executable under a moved-aside *.app.bkp (#54170, before fleetd deletes
    its temp dir); and a process whose executable was deleted, which osquery reports with an empty path but the
    original command line (checked on a copied app, moved aside and deleted while running: path "", on_disk -1).
    Windows: the exe, plus the pids open_app saw under the exe's name: an installer that renames the exe in use
    leaves the old process running, and osquery reports it at the new name while Get-Process keeps the old path
    (checked on host 228: pingtest.exe renamed to pingtest.exe.moved while running). Those rows get `moved`."""
    if t["plat"] == "darwin":
        app, exe_name = t["app"].replace("'", "''"), t["exe"].rsplit("/", 1)[-1].replace("'", "''")
        bkp = app.rsplit("/", 1)[-1] + ".bkp"
        q = (f"SELECT pid, path, on_disk FROM processes WHERE path LIKE '{app}/Contents/MacOS/%' "
             f"OR path LIKE '%/{bkp}/Contents/MacOS/{exe_name}' "
             f"OR (path = '' AND cmdline LIKE '{app}/Contents/MacOS/%');")
    else:
        if not t.get("exe"):
            return []
        exe = t["exe"].replace("'", "''")
        pids = ",".join(x for x in t.get("pids", []) if x.isdigit())
        q = (f"SELECT pid, path, on_disk FROM processes WHERE lower(path) = lower('{exe}')" +
             (f" OR (pid IN ({pids}) AND lower(name) = lower('{exe.rsplit(chr(92), 1)[-1]}'))" if pids else "") + ";")
    for _ in range(3):
        st, d = R.api("POST", f"/hosts/{host}/query", {"query": q}, timeout=180)
        if st == 200 and not d.get("error"):
            return [{"pid": str(r.get("pid")), "path": R.scrub(r.get("path", "")), "on_disk": str(r.get("on_disk")),
                     **({"moved": True} if t["plat"] == "windows" and r.get("path", "").lower() != t["exe"].lower() else {})}
                    for r in d.get("rows") or []]
        time.sleep(15)
    return None


def current(p):
    """Running from the installed files (not deleted, not a moved-aside copy)."""
    return bool(p["path"]) and p["on_disk"] != "0" and ".bkp/" not in p["path"] and not p.get("moved")


def judge_open(rec, find, t, key, label, before, after, status, lines):
    """The app was open when the install ran. `before`: its pids then; `after`: processes() after the install.
    `key` prefixes what goes in the record, `label` starts each finding."""
    if after is None:
        rec[f"{key}_running_note"] = "host didn't answer the process query after the install"
        return
    said = " ".join(lines)
    stale = [p for p in after if not current(p)]
    kept = [p for p in after if p["pid"] in before and p not in stale]
    fresh = [p for p in after if p["pid"] not in before and p not in stale]
    if t["plat"] == "windows":
        if stale:
            # Windows can't delete an exe in use, so installers rename it; Fleet's Windows scripts don't close apps
            rec[f"{key}_running_note"] = (f"the old copy kept running from its renamed files ({stale[0]['path']}, "
                                          f"pid {stale[0]['pid']}); expected on Windows")
        if status != "installed":
            find("S6", f"{label}: the install over the open app ended as {status}")
        return
    if stale:
        find("S6", f"{label}: Fleet says {status}, but the app is still running from files the install deleted "
                   f"({stale[0]['path'] or 'its executable is gone'}, pid {stale[0]['pid']}) (#54170 shape)")
    if status == "installed":
        if kept:
            find("S6", f"{label}: the app was replaced while running and never quit (pid {kept[0]['pid']} survived) "
                       f"(#55338 shape)")
        elif not fresh and not stale:
            tried = "the script said it would relaunch it" if "relaunch" in said else "the script didn't try to"
            find("S6", f"{label}: the install closed the open app and didn't reopen it; {tried} (#56809 shape)")
    elif "did not quit" in said:
        # the guard working as designed: the app wouldn't quit, so the script left it alone
        rec[f"{key}_running_note"] = "install refused because the app didn't quit (by design); app left running"
    else:
        find("S6", f"{label}: the install over the open app ended as {status}")


def judge_closed(rec, find, key, label, after):
    """The app wasn't open when the update ran; it shouldn't be running afterwards."""
    if after is None:
        rec[f"{key}_running_note"] = "host didn't answer the process query after the update"
    elif [p for p in after if current(p)]:
        find("S6", f"{label}: the app wasn't open, but it's running after the update (#48638 shape)")


def starts(host, t, rec, find, key, label, seen_running):
    """Opens the app (unless it's already running from its current files) and checks it stays up, then closes it.
    A failure is a finding only if this record already saw the app start (seen_running), otherwise a note."""
    now = processes(host, t) or []
    if t["plat"] == "windows" and now and not any(current(p) for p in now):
        # only the old copy, running from renamed files: opening the app again just hands off to it (single-instance
        # apps: Firefox, Brave, Mullvad), so close it first and open the new build
        close_app(host, t, rec, f"{key}_old")
    ok = any(current(p) for p in now) or open_app(host, t, rec, key)
    if ok:
        time.sleep(10)
        later = processes(host, t)
        if later is None:
            # the live query timed out: that says nothing about the app (read as [] it made false S6s on host 50)
            rec[f"{key}_starts"] = None
            rec[f"{key}_running_note"] = "host didn't answer the process query after opening; not judged"
            close_app(host, t, rec, key)
            return
        ok = any(current(p) for p in later)
    rec[f"{key}_starts"] = ok
    if not ok:
        if seen_running:
            find("S6", f"{label}: the app doesn't stay open after the install, though it opened before (#32227 shape)")
        else:
            rec[f"{key}_running_note"] = "couldn't open the app; not judged"
    close_app(host, t, rec, key)


def reinstall_open(host, tid, rec, find, plat, ctx):
    """Cycle step: open the freshly installed app, install it again over the open copy, judge, close it."""
    t = target(plat, ctx)
    if not t:
        rec["open_reinstall"] = "skipped: no app path or name to open"
        return
    if not open_app(host, t, rec, "open_reinstall_before"):
        rec["open_reinstall"] = f"skipped: couldn't open the app ({rec['open_reinstall_before_open']})"
        close_app(host, t, rec, "open_reinstall_before")
        return
    before = set(rec["open_reinstall_before_open"]["pids"])
    R.install(host, tid, rec, "open_reinstall")
    after = processes(host, t)
    rec["open_reinstall_processes_after"] = after
    judge_open(rec, find, t, "open_reinstall", "reinstall over the open app", before, after, rec.get("open_reinstall_status"),
               rec.get("open_reinstall_app_lines", []))
    starts(host, t, rec, find, "open_reinstall_after", "after reinstalling over the open app", seen_running=True)
    rec["open_reinstall"] = "done"
