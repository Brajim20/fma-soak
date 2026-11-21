"""False-success lane (#58221, #51123, #50056): does each FMA's install script fail when its installer is broken?

Fleet reports an install as Success when the install script exits 0, so a script that doesn't check a step (ditto -x
in #58221, hdiutil attach + cp in #51123) turns a broken download into a green checkmark with nothing installed. The
lifecycle cycles can't see this: their downloads are never broken. Fleet won't take a broken zip or DMG as a custom
package either (pkg/file only parses pkg/msi/exe/deb/rpm/ipa/tar.gz), so this lane runs the catalog's own install
script on the host the way fleetd does (INSTALLER_PATH, root/SYSTEM) with a broken installer in place of the real
one, and checks that it exits non-zero.

Broken installers, by extension: a zip is a real archive whose entry data is overwritten in the middle, central
directory intact (the tool starts, then fails partway, as in #58221); a dmg is a real image cut in half; everything
else is random bytes.

Verdicts per app:
  fails         exit != 0: what Fleet needs to report Failed
  FALSE SUCCESS exit 0 with a broken installer: Fleet would show Success; a finding (S3)
  hangs         still running after hang_seconds: Fleet would hold the host's queue until the script timeout; a finding
  skipped       the app is already on the host (the script could replace or remove a copy the harness didn't install)

Each day, per platform, `per_day` apps (config scenarios.false_success): apps never checked or whose install script
changed first, then the longest since checked, round-robin over installer types so every type runs each day. The
whole catalog comes round in about (catalog / per_day) days. State: results/state/false-success-<plat>.json
{slug: {"date", "script", "verdict"}}; the day's run: results/scenarios/<date>-false-success-<plat>.json. A finding
also gets a soak record (kind "false_success") so the digest and triage.json handle it; those records aren't
lifecycle cycles for the coverage ledger.

  python3 falsesuccess.py --platform darwin             # what pass.sh runs (skips if already done today)
  python3 falsesuccess.py --platform windows --slug 7-zip/windows   # one app, ignores the daily stamp
"""
import argparse, base64, datetime, gzip, hashlib, json, os, re, subprocess, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R

CFG = R.CFG.get("scenarios", {}).get("false_success", {})
OUT = os.path.join(R.WS, "results", "scenarios")
HANG = CFG.get("hang_seconds", 240)

DARWIN = r'''#!/bin/bash
D=/private/tmp/fleet-qa-fs; rm -rf "$D"; mkdir -p "$D/src/__APP__/Contents/MacOS"
printf '<?xml version="1.0"?><plist version="1.0"><dict/></plist>' > "$D/src/__APP__/Contents/Info.plist"
F="$D/__FNAME__"
head -c 2097152 /dev/urandom > "$D/src/__APP__/Contents/MacOS/blob"
case "__KIND__" in
  zip) ditto -c -k --keepParent "$D/src/__APP__" "$D/full" ;;
  dmg) hdiutil create -quiet -srcfolder "$D/src" -volname "QA Probe" -format UDZO "$D/full.dmg" && mv "$D/full.dmg" "$D/full" ;;
  *)   head -c 65536 /dev/urandom > "$D/full" ;;
esac
cp "$D/full" "$F"
sz=$(stat -f%z "$F")
case "__KIND__" in
  zip) dd if=/dev/urandom of="$F" bs=1024 seek=$(( sz / 3072 )) count=256 conv=notrunc 2>/dev/null ;;  # data, not the directory
  dmg) head -c $(( sz / 2 )) "$D/full" > "$F" ;;
esac
echo '__B64__' | base64 -D | gunzip > "$D/install.sh"
A="/Applications/__APP__"; PRE=0; [ -e "$A" ] && PRE=1
cd "$D"
INSTALLER_PATH="$F" /bin/bash "$D/install.sh" > "$D/out.txt" 2>&1 &
pid=$!
for i in $(seq 1 __HANG__); do kill -0 $pid 2>/dev/null || break; sleep 1; done
if kill -0 $pid 2>/dev/null; then pkill -TERM -P $pid; kill -TERM $pid; sleep 2; pkill -KILL -P $pid; kill -KILL $pid; echo "FS_HANG"; fi
wait $pid; code=$?
tail -c 1500 "$D/out.txt"; echo; echo "FS_EXIT=$code"
hdiutil info | grep -A25 "image-path.*fleet-qa-fs" | awk '/^\/dev\/disk[0-9]+[ \t]/{print $1}' | sort -u | while read -r d; do hdiutil detach -force "$d" >/dev/null 2>&1; done
# a script that carried on after the broken extraction copies the half-written app: remove it, but only when it
# wasn't there before and holds nothing but the probe's own files
if [ $PRE = 0 ] && [ -d "$A" ] && [ -z "$(find "$A" -type f ! -name blob ! -name Info.plist | head -1)" ]; then
  rm -rf "$A"; echo "FS_LEFTOVER removed $A"
fi
rm -rf "$D"
'''

WINDOWS = r'''
$D = "C:\Windows\Temp\fleet-qa-fs"
Remove-Item -Recurse -Force $D -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $D | Out-Null
$F = Join-Path $D "__FNAME__"
if ("__KIND__" -eq "zip") {
  $src = Join-Path $D "src"; New-Item -ItemType Directory -Force -Path $src | Out-Null
  $b = New-Object byte[] 2097152; (New-Object Random).NextBytes($b); [IO.File]::WriteAllBytes((Join-Path $src "QAProbe.exe"), $b)
  Compress-Archive -Path "$src\*" -DestinationPath (Join-Path $D "full.zip")
  $full = [IO.File]::ReadAllBytes((Join-Path $D "full.zip"))
  $junk = New-Object byte[] 262144; (New-Object Random).NextBytes($junk)
  [Array]::Copy($junk, 0, $full, [int]($full.Length / 3), [Math]::Min($junk.Length, [int]($full.Length / 3)))
  [IO.File]::WriteAllBytes($F, $full)  # entry data corrupted, central directory intact: extraction fails partway
} else {
  $b = New-Object byte[] 65536; (New-Object Random).NextBytes($b); [IO.File]::WriteAllBytes($F, $b)
}
$gz = New-Object IO.Compression.GZipStream((New-Object IO.MemoryStream(,[Convert]::FromBase64String("__B64__"))), [IO.Compression.CompressionMode]::Decompress)
$sr = New-Object IO.StreamReader($gz, [Text.Encoding]::UTF8); [IO.File]::WriteAllText((Join-Path $D "install.ps1"), $sr.ReadToEnd()); $sr.Close()
$env:INSTALLER_PATH = $F
$p = Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$D\install.ps1`"" -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $D "out.txt") -RedirectStandardError (Join-Path $D "err.txt")
$null = $p.Handle
if (-not $p.WaitForExit(__HANG__ * 1000)) { & taskkill.exe /T /F /PID $p.Id | Out-Null; Write-Output "FS_HANG" }
$p.WaitForExit()
Get-Content (Join-Path $D "out.txt"), (Join-Path $D "err.txt") -Tail 25 -ErrorAction SilentlyContinue
Write-Output "FS_EXIT=$($p.ExitCode)"
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like "*__FNAME__*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $D -ErrorAction SilentlyContinue
'''


def catalog(plat):
    out = subprocess.run(["git", "-C", R.REPO, "ls-tree", "-r", "--name-only", "origin/main", "ee/maintained-apps/outputs/"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted(f"{p.split('/')[-2]}/{plat}" for p in out if p.endswith(f"/{plat}.json"))


def kind_of(fname, script):
    """The installer type from the file name, or (URLs like VS Code's ".../stable" have no extension) from what the
    script does with it."""
    low = fname.lower()
    k = next((k for k in ("zip", "dmg", "pkg", "msi", "msix", "exe") if low.endswith("." + k)), None)
    if k:
        return k
    for pat, k in ((r"ditto -x -k|unzip |Expand-Archive|ZipFile", "zip"), (r"hdiutil attach", "dmg"),
                   (r"installer -pkg", "pkg"), (r"msiexec", "msi"), (r"Add-AppxPackage", "msix")):
        if re.search(pat, script):
            return k
    return "exe" if "Start-Process" in script else "other"


def teeth(plat="darwin"):
    """The lane must catch a known false success: VS Code's install script from before PR #58348 (the #58221 fix),
    which ran `ditto -x -k` without checking it. Run once a day with the day's probes; anything but FALSE SUCCESS
    means the probe lost its teeth (broken installer not broken enough, or the script never reached ditto)."""
    ref = CFG.get("teeth_ref", "baf34ea1900c0d1a494093da6082a1ba325880d9^1")
    path = f"{ref}:ee/maintained-apps/outputs/visual-studio-code/darwin.json"
    show = lambda: subprocess.run(["git", "-C", R.REPO, "show", path], capture_output=True, text=True)
    r = show()
    if r.returncode:  # CI's catalog clone is shallow (10 days): fetch the commit and its parent
        subprocess.run(["git", "-C", R.REPO, "fetch", "-q", "--depth", "2", "origin", ref.split("^")[0]], capture_output=True)
        r = show()
    if r.returncode:
        return {"verdict": "unavailable", "detail": f"couldn't read the pre-fix script: {r.stderr.strip()[:160]}", "ok": None}
    j = json.loads(r.stdout)
    v = j["versions"][0]
    script = j["refs"][v["install_script_ref"]]
    host = R.CFG["hosts"][plat][0]
    exists = v.get("queries", {}).get("exists")
    if exists and R.present(host, exists):
        return {"verdict": "skipped", "detail": "VS Code is on the host"}
    res = R.run_script(host, probe_body(plat, "zip", "VSCode-darwin-universal.zip", script), timeout=HANG + 300)
    vd, msg = verdict(res)
    return {"verdict": vd, "detail": msg, "ok": vd == "FALSE SUCCESS", "ref": ref}


def target(slug):
    """(kind, installer file name, install script, script sha) from the catalog's newest version."""
    m = R.catalog_manifest(slug)
    v = m["versions"][0]
    fname = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(v["installer_url"]).path)) or "installer"
    fname = "".join(c for c in fname if c.isalnum() or c in "._- ()")[:80] or "installer"
    script = m["refs"][v["install_script_ref"]]
    kind = kind_of(fname, script)
    return kind, fname, script, hashlib.sha256(script.encode()).hexdigest()[:12], v.get("queries", {}).get("exists")


def app_name(script):
    """The .app the script copies out of the archive or image ("$TMPDIR/Visual Studio Code.app"), so a partial
    extraction leaves a half-written copy of the right app, as the damaged download in #58221 did."""
    m = re.search(r'/([^/"\'$`*]+\.app)(?=["/\s])', script)
    name = m.group(1).strip() if m else "QA Probe.app"
    return "".join(c for c in name if c.isalnum() or c in " ._-+()") or "QA Probe.app"


def probe_body(plat, kind, fname, script):
    t = DARWIN if plat == "darwin" else WINDOWS
    t = t.replace("__APP__", app_name(script))
    return (t.replace("__FNAME__", fname.replace("'", "").replace('"', "")).replace("__KIND__", kind)
             .replace("__HANG__", str(HANG)).replace("__B64__", base64.b64encode(gzip.compress(script.encode(), mtime=0)).decode()))  # ad-hoc scripts: 10,000 chars


def verdict(res):
    out = res.get("output", "")
    if "error" in res and "exit_code" not in res:
        return "error", res["error"]
    code = next((l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith("FS_EXIT=")), None)
    if "FS_HANG" in out:
        return "hangs", f"install script still running {HANG}s after starting with a broken installer"
    if code is None:
        return "error", "no FS_EXIT line (probe didn't finish)"
    if code == "0":
        left = " (it copied a half-written app into /Applications; the probe removed it)" if "FS_LEFTOVER" in out else ""
        return "FALSE SUCCESS", "install script exited 0 with a broken installer: Fleet would report Success" + left
    lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith(("FS_EXIT=", "FS_HANG", "FS_LEFTOVER"))]
    return "fails", f"exit {code}" + (f": {lines[-1][:160]}" if lines else "")


def state_path(plat):
    return os.path.join(R.WS, "results", "state", f"false-success-{plat}.json")


def pick(plat, ledger, n):
    rows = []
    for slug in catalog(plat):
        try:
            kind, fname, script, sha, exists = target(slug)
        except Exception:
            continue
        e = ledger.get(slug)
        fresh = not e or e.get("script") != sha
        rows.append((not fresh, (e or {}).get("date", ""), kind, slug, fname, script, sha, exists))
    rows.sort()
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r[2], []).append(r)
    out, kinds = [], sorted(by_kind, key=lambda k: by_kind[k][0][:2])
    while len(out) < n and any(by_kind.values()):
        for k in kinds:
            if by_kind[k] and len(out) < n:
                out.append(by_kind[k].pop(0))
    return out


def record(slug, host, plat, kind, fname, sha, v, msg, res, started):
    rec = {"slug": slug, "host": host, "kind": "false_success", "started": started, "build": R.build(),
           "installer_kind": kind, "installer_file": fname, "install_script_sha": sha,
           "findings": [{"shape": "S3", "msg": f"broken {kind} installer: {msg}"}], "outcome": "findings",
           "probe_output_tail": res.get("output", "")[-1500:]}
    return R.finish(rec)


def recheck(slug, host):
    """recheck.py: rerun the probe for a filed false-success bug. A record either way: outcome pass when the script
    now fails with the broken installer."""
    plat = slug.split("/")[1]
    kind, fname, script, sha, exists = target(slug)
    started = datetime.datetime.now().isoformat(timespec="seconds")
    if exists and R.present(host, exists):
        return R.finish({"slug": slug, "host": host, "kind": "false_success", "started": started, "build": R.build(),
                         "findings": [], "outcome": "skipped: already on the host"})
    res = R.run_script(host, probe_body(plat, kind, fname, script), timeout=HANG + 300)
    v, msg = verdict(res)
    if v in ("FALSE SUCCESS", "hangs"):
        return record(slug, host, plat, kind, fname, sha, v, msg, res, started)
    return R.finish({"slug": slug, "host": host, "kind": "false_success", "started": started, "build": R.build(),
                     "installer_kind": kind, "install_script_sha": sha, "findings": [],
                     "outcome": "pass" if v == "fails" else f"error: {msg}", "probe_output_tail": res.get("output", "")[-1500:]})


def run(plat, slug=None):
    host = R.CFG["hosts"][plat][0]
    date = datetime.date.today().isoformat()
    os.makedirs(OUT, exist_ok=True)
    day = os.path.join(OUT, f"{date}-false-success-{plat}.json")
    if os.path.exists(day) and not slug:
        print(f"false-success {plat}: already ran today ({day})")
        return
    ledger = json.load(open(state_path(plat))) if os.path.exists(state_path(plat)) else {}
    if slug:
        kind, fname, script, sha, exists = target(slug)
        todo = [(None, None, kind, slug, fname, script, sha, exists)]
    else:
        todo = pick(plat, ledger, CFG.get("per_day", {}).get(plat, 30 if plat == "darwin" else 15))
    run_rec = {"date": date, "host": host, "build": R.build(), "hang_seconds": HANG, "apps": []}
    if plat == "darwin" and not slug:
        try:
            run_rec["teeth"] = teeth(plat)
        except Exception as e:
            run_rec["teeth"] = {"verdict": "error", "detail": f"{type(e).__name__}: {e}", "ok": None}
        print(f"false-success teeth (pre-#58348 VS Code script): {run_rec['teeth']['verdict']} ({run_rec['teeth']['detail']})")
        if run_rec["teeth"].get("ok") is False:  # the digest shows it as a harness problem
            R.finish({"slug": "visual-studio-code/darwin", "host": host, "kind": "false_success",
                      "started": datetime.datetime.now().isoformat(timespec="seconds"), "build": R.build(),
                      "outcome": "findings", "findings": [{"shape": "harness", "msg": "false-success probe lost its teeth: "
                      f"the pre-#58348 VS Code script came back {run_rec['teeth']['verdict']} ({run_rec['teeth']['detail']})"}]})
    for _, _, kind, s, fname, script, sha, exists in todo:
        started = datetime.datetime.now().isoformat(timespec="seconds")
        if exists and R.present(host, exists):
            v, msg, res = "skipped", "already on the host", {}
        else:
            res = R.run_script(host, probe_body(plat, kind, fname, script), timeout=HANG + 300)
            v, msg = verdict(res)
            if exists and v != "error" and R.present(host, exists):
                msg += "; the app is on the host afterwards"
        row = {"slug": s, "kind": kind, "script": sha, "verdict": v, "detail": msg}
        if v in ("FALSE SUCCESS", "hangs"):
            row["record"] = record(s, host, plat, kind, fname, sha, v, msg, res, started).get("record")
        if v != "error":
            ledger[s] = {"date": date, "script": sha, "verdict": v}
        run_rec["apps"].append(row)
        print(f"   {s} [{kind}]: {v} ({msg})")
    os.makedirs(os.path.dirname(state_path(plat)), exist_ok=True)
    json.dump(ledger, open(state_path(plat), "w"), indent=1, sort_keys=True)
    json.dump(run_rec, open(day if not slug else day.replace(".json", f"-{slug.split('/')[0]}.json"), "w"), indent=1)
    tally = {}
    for a in run_rec["apps"]:
        tally[a["verdict"]] = tally.get(a["verdict"], 0) + 1
    print(f"false-success {plat} on {host}: {tally}; checked so far {len(ledger)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["darwin", "windows"])
    ap.add_argument("--slug")
    ap.add_argument("--teeth", action="store_true", help="only the known-false-success check")
    a = ap.parse_args()
    print(teeth(a.platform)) if a.teeth else run(a.platform, a.slug)
