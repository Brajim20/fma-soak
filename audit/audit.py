#!/usr/bin/env python3
"""L0 catalog audit for Fleet-maintained apps (fleetdm/fleet#55961).

Reads the FMA catalog from a git ref (default origin/main, which is what Fleet
servers download), runs checks, and writes results/audit-<date>.json + .md.

  python3 audit.py                 offline checks only (seconds)
  python3 audit.py --network       + freshness, URL and SHA-256 checks
  python3 audit.py --network --hash-sample 20

Checks (tracker task -> escapes):
  1.2 duplicates        #53287
  1.3 freshness         #52746 #55546   (--network)
  1.4 url + sha256      #55360          (--network)
  1.5 script stability  #51577
  1.6 version shape     #50968
  1.7 windows scope     #50857 #51138
"""
import argparse, datetime, hashlib, json, os, re, subprocess, sys, urllib.request, urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import cmp_to_key

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import settings
CFG = json.load(open(os.path.join(HERE, "..", "config.json")))  # catalog-only: no instance settings needed
REPO = settings.catalog_repo({**CFG, **(json.load(open(os.path.join(HERE, "..", "config.local.json"))) if os.path.exists(os.path.join(HERE, "..", "config.local.json")) else {})})  # the fleet checkout (CI: sparse clone)
OUT_DIR = "ee/maintained-apps/outputs"
IN_DIR = "ee/maintained-apps/inputs"
UA = {"User-Agent": "fleet-qa-fma-audit"}


# ---------- catalog loading ----------

def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, check=True).stdout


def load_catalog(ref):
    """Return (sha, apps, manifests, inputs) for the given ref."""
    sha = git("rev-parse", "--short", ref).strip()
    apps = json.loads(git("show", f"{ref}:{OUT_DIR}/apps.json"))["apps"]
    paths = [p for p in git("ls-tree", "-r", "--name-only", ref, OUT_DIR, IN_DIR).split() if p.endswith(".json")]
    # one `git cat-file --batch` call instead of ~2,900 `git show`s
    proc = subprocess.run(["git", "-C", REPO, "cat-file", "--batch"], input="".join(f"{ref}:{p}\n" for p in paths).encode(),
                          capture_output=True, check=True)
    blobs, buf, pos = {}, proc.stdout, 0
    for p in paths:
        nl = buf.index(b"\n", pos)
        size = int(buf[pos:nl].split()[2])
        blobs[p] = buf[nl + 1:nl + 1 + size].decode("utf-8", errors="replace")
        pos = nl + 1 + size + 1  # content is followed by a newline
    manifests, inputs = {}, {}
    for a in apps:
        base, plat = a["slug"].split("/")
        m = blobs.get(f"{OUT_DIR}/{base}/{plat}.json")
        if m:
            manifests[a["slug"]] = json.loads(m)
        src = "homebrew" if plat == "darwin" else "winget"
        i = blobs.get(f"{IN_DIR}/{src}/{base}.json")
        if i:
            inputs[a["slug"]] = json.loads(i)
    return sha, apps, manifests, inputs


def last_changed(ref, slug):
    base, plat = slug.split("/")
    out = git("log", "-1", "--format=%cs", ref, "--", f"{OUT_DIR}/{base}/{plat}.json").strip()
    return out or None


# ---------- version helpers ----------

def vparts(v):
    """Split a version into comparable parts: digit runs as ints, letter runs as strings ("1.23b" -> 1, 23, "b")."""
    return [int(p) if p.isdigit() else p for p in re.findall(r"\d+|[a-z]+", str(v).split(",")[0].lower())]


def same_release(ours, up):
    """8.12.36 vs 8.12.36.40, or 1.2 vs 1.2.0: the catalog keeps fewer parts than upstream."""
    a, b = vparts(ours), vparts(up)
    return len(b) >= len(a) and b[:len(a)] == a


def vcmp(a, b):
    pa, pb = vparts(a), vparts(b)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        return -1 if str(x) < str(y) else 1
    return (len(pa) > len(pb)) - (len(pa) < len(pb))


def vnorm(v):
    return ".".join(str(p) for p in vparts(v))


# ---------- offline checks ----------

def check_duplicates(apps):
    """1.2: same display name under different slug bases (shows as two entries, #53287),
    and one slug base carrying different names per platform."""
    by_name, by_base = defaultdict(set), defaultdict(set)
    for a in apps:
        base = a["slug"].split("/")[0]
        by_name[a["name"].strip().lower()].add(a["slug"])
        by_base[base].add(a["name"])
    out = []
    for name, slugs in by_name.items():
        bases = {s.split("/")[0] for s in slugs}
        if len(bases) > 1:
            out.append({"kind": "same_name_different_slug", "name": name, "slugs": sorted(slugs)})
    # The UI strips " for macOS|Mac|Windows" when that's the only difference (FleetMaintainedAppsTable.tsx),
    # so only flag names that still differ after stripping.
    suffix = re.compile(r" for (macOS|Mac|Windows)$")
    for base, names in by_base.items():
        if len({suffix.sub("", n) for n in names}) > 1:
            out.append({"kind": "same_slug_different_name", "slug_base": base, "names": sorted(names)})
    return out


def check_versions(manifests):
    """1.6: version shape and ordering inside each manifest."""
    out = []
    for slug, m in manifests.items():
        vs = [v.get("version", "") for v in m.get("versions", [])]
        for v in vs:
            if not v or v.lower() in ("latest", "unknown"):
                out.append({"slug": slug, "kind": "no_real_version", "version": v})
            elif not re.match(r"^\d", v):
                out.append({"slug": slug, "kind": "version_not_numeric_start", "version": v})
        if len(vs) > 1 and any(vcmp(vs[i], vs[i + 1]) < 0 for i in range(len(vs) - 1)):
            out.append({"slug": slug, "kind": "versions_not_newest_first", "versions": vs})
    return out


def check_version_regressions(ref, manifests, days):
    """1.6: compare each app's version with the catalog `days` ago; a lower version now is a red flag."""
    old_sha = git("rev-list", "-1", f"--before={days} days ago", ref).strip()
    if not old_sha:
        return [], None
    _, _, old_manifests, _ = load_catalog(old_sha)
    out = []
    for slug, m in manifests.items():
        o = old_manifests.get(slug)
        if not o or not m.get("versions") or not o.get("versions"):
            continue
        new, old = m["versions"][0]["version"], o["versions"][0]["version"]
        if vcmp(new, old) < 0:
            out.append({"slug": slug, "kind": "version_went_down", "from": old, "to": new})
    return out, old_sha[:10]


def check_scripts(manifests):
    """1.5: scripts that hardcode the version change on every release, which is what tripped the
    auto-update cron's 'admin edited this script' logic (#51577). Also installer extension sanity."""
    out = []
    for slug, m in manifests.items():
        if not m.get("versions"):
            continue
        v = m["versions"][0]
        ver, refs = v.get("version", ""), m.get("refs", {})
        for kind in ("install_script_ref", "uninstall_script_ref"):
            body = refs.get(v.get(kind, ""), "")
            if ver and len(ver) >= 3 and ver in body:
                lines = [l.strip() for l in body.splitlines() if ver in l][:2]
                out.append({"slug": slug, "kind": f"{kind.split('_')[0]}_script_hardcodes_version", "version": ver, "lines": lines})
            if not body:
                out.append({"slug": slug, "kind": f"{kind.split('_')[0]}_script_missing"})
    return out


def check_windows_scope(inputs):
    """1.7: Windows apps whose installer scope is user land under SYSTEM's profile when fleetd installs them."""
    out = []
    for slug, i in inputs.items():
        if slug.endswith("/windows") and str(i.get("installer_scope", "")).lower() == "user":
            out.append({"slug": slug, "kind": "user_scope_installer", "package_identifier": i.get("package_identifier")})
    return out


def check_frozen(ref, inputs):
    """1.3: inputs marked `frozen` are skipped by the nightly ingest without an error. #55546 was a freeze
    that outlived its reason. List each one with when (and in which commit) it was frozen."""
    out, today = [], datetime.date.today()
    for slug, i in sorted(inputs.items()):
        if not i.get("frozen"):
            continue
        base, plat = slug.split("/")
        src = "homebrew" if plat == "darwin" else "winget"
        log = git("log", "--format=%h %cs %s", "-S", '"frozen"', ref, "--", f"{IN_DIR}/{src}/{base}.json").strip().splitlines()
        first = log[-1].split(" ", 2) if log else None  # oldest commit that touched the flag
        out.append({"slug": slug, "kind": "frozen_input",
                    "frozen_since": first[1] if first else None,
                    "days_frozen": (today - datetime.date.fromisoformat(first[1])).days if first else None,
                    "commit": f"{first[0]} {first[2][:70]}" if first else None})
    return out


def hashless(manifests):
    return sorted(s for s, m in manifests.items() if m.get("versions") and m["versions"][0].get("sha256") in ("no_check", "", None))


# ---------- network checks ----------

def http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def brew_latest(token):
    try:
        return http_json(f"https://formulae.brew.sh/api/cask/{token}.json").get("version")
    except Exception as e:
        return f"ERR {type(e).__name__}"


def winget_latest(pkg_id):
    """Highest version folder under manifests/<l>/<Publisher>/<Name...> in microsoft/winget-pkgs."""
    parts = pkg_id.split(".")
    path = f"manifests/{parts[0][0].lower()}/" + "/".join(parts)
    r = subprocess.run(["gh", "api", f"repos/microsoft/winget-pkgs/contents/{path}", "--jq", '.[] | select(.type=="dir") | .name'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return f"ERR {r.stderr.strip()[:60]}"
    # Folders are either versions (holding *.yaml) or child packages (Adobe/Acrobat/Pro/2020, Google/Chrome/Dev).
    # Try candidates newest-first and accept the first that holds manifests.
    cands = sorted((d for d in r.stdout.split() if re.match(r"^\d", d)), key=cmp_to_key(vcmp), reverse=True)
    for d in cands[:4]:
        q = subprocess.run(["gh", "api", f"repos/microsoft/winget-pkgs/contents/{path}/{d}", "--jq", '[.[] | select(.name|endswith(".yaml"))] | length'],
                           capture_output=True, text=True)
        if q.returncode == 0 and q.stdout.strip() not in ("", "0"):
            return d
    return "ERR no versions"


def check_freshness(ref, apps, manifests, inputs, lag_days):
    """1.3: compare with upstream (Homebrew cask / winget) and flag macOS/Windows divergence."""
    jobs = {}
    for a in apps:
        s, i = a["slug"], inputs.get(a["slug"], {})
        if s.endswith("/darwin") and i.get("token"):
            jobs[s] = ("brew", i["token"])
        elif s.endswith("/windows") and i.get("package_identifier"):
            jobs[s] = ("winget", i["package_identifier"])
    def work(item):
        s, (src, key) = item
        return s, src, (brew_latest(key) if src == "brew" else winget_latest(key))
    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(work, jobs.items()))
    stale, errors, today = [], [], datetime.date.today()
    for s, src, up in results:
        ours = manifests.get(s, {}).get("versions", [{}])[0].get("version", "")
        if up is None or str(up).startswith("ERR"):
            errors.append({"slug": s, "source": src, "error": up})
            continue
        if str(up) == "latest" or vnorm(up) == vnorm(ours) or vcmp(up, ours) <= 0 or same_release(ours, up):
            continue
        changed = last_changed(ref, s)
        age = (today - datetime.date.fromisoformat(changed)).days if changed else None
        if age is None or age >= lag_days:
            stale.append({"slug": s, "kind": "behind_upstream", "source": src, "ours": ours, "upstream": up,
                          "catalog_unchanged_days": age, "frozen": bool(inputs.get(s, {}).get("frozen"))})
    # #52746 shape: Windows behind macOS on the same version scheme, and winget itself has nothing newer
    # (upstream drift Fleet can't pick up). Vendors releasing on different days is not flagged.
    up_by_slug = {s: up for s, _, up in results}
    by_base = defaultdict(dict)
    for s, m in manifests.items():
        if m.get("versions"):
            by_base[s.split("/")[0]][s.split("/")[1]] = m["versions"][0]["version"]
    diverge = []
    for b, v in by_base.items():
        if "darwin" not in v or "windows" not in v:
            continue
        mac, win = vparts(v["darwin"]), vparts(v["windows"])
        if len(mac) != len(win) or not mac or mac[0] != win[0] or vcmp(v["windows"], v["darwin"]) >= 0:
            continue
        wup = up_by_slug.get(f"{b}/windows")
        if wup and not str(wup).startswith("ERR") and vcmp(wup, v["windows"]) <= 0:
            diverge.append({"slug_base": b, "darwin": v["darwin"], "windows": v["windows"], "winget_latest": wup})
    return stale, diverge, errors


def head(url):
    """First byte via curl (system certificate store, plain curl user agent). A 403 here can still mean a
    vendor that only serves browsers; those go to manual check, never worked around."""
    # -g: installer URLs can contain [ ] (CodeMeter), which curl would otherwise treat as a glob
    r = subprocess.run(["curl", "-g", "-s", "-o", "/dev/null", "-L", "-r", "0-0", "-m", "30", "-w", "%{http_code} %{content_type}", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return f"ERR curl exit {r.returncode}" + (" (host not found)" if r.returncode == 6 else "")
    code, _, ctype = (r.stdout or "0").partition(" ")
    # an installer URL that answers with a web page means the download moved; a pinned SHA-256 can't match it
    if int(code) < 400 and ctype.lower().startswith("text/html"):
        return "HTML page instead of an installer"
    return int(code)


def check_urls(manifests):
    """1.4: every installer URL answers."""
    items = [(s, m["versions"][0]["installer_url"]) for s, m in manifests.items() if m.get("versions") and m["versions"][0].get("installer_url")]
    ok = lambda st: isinstance(st, int) and st < 400
    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(lambda it: (it[0], it[1], head(it[1])), items))
    # retry failures one at a time so parallel load and slow vendors don't count as broken
    out = []
    for s, u, st in res:
        if ok(st):
            continue
        st2 = head(u)
        if ok(st2):
            continue
        # Only a host that no longer resolves is conclusive. A 404, 403 or web page to curl can be the vendor
        # treating curl differently from Fleet's server: Rocket and Vanilla 404 to curl but added fine through
        # Fleet (hash matched), and so did Comet, Fetch and jamovi, which serve curl a web page (2026-10-29).
        # Those are suspects to confirm by adding the app through Fleet.
        dead = "host not found" in str(st2)
        html = str(st2).startswith("HTML page") or st2 in (404, 410)
        blocked = st2 in (401, 403)
        kind = ("url_dead" if dead else "url_html_confirm_via_fleet" if html
                else "url_blocked_needs_manual_check" if blocked else "url_unreachable")
        out.append({"slug": s, "kind": kind, "url": u, "status": st2})
    return out


def check_hashes(manifests, sample, rotation_days):
    """1.4: download and hash a rotating slice of hashed apps; over `rotation_days` every app is covered."""
    hashed = sorted(s for s, m in manifests.items() if m.get("versions") and m["versions"][0].get("sha256") not in ("no_check", "", None))
    day = datetime.date.today().toordinal() % rotation_days
    todays = [s for i, s in enumerate(hashed) if i % rotation_days == day][:sample]
    def work(s):
        v = manifests[s]["versions"][0]
        h = hashlib.sha256()
        p = subprocess.Popen(["curl", "-s", "-f", "-L", "-m", "600", v["installer_url"]], stdout=subprocess.PIPE)
        for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
            h.update(chunk)
        if p.wait() != 0:
            return {"slug": s, "kind": "download_failed", "error": f"curl exit {p.returncode}"}
        got = h.hexdigest()
        return None if got == v["sha256"] else {"slug": s, "kind": "sha256_mismatch", "expected": v["sha256"], "got": got}
    with ThreadPoolExecutor(4) as ex:
        res = [r for r in ex.map(work, todays) if r]
    return res, todays


# ---------- report ----------

def write_report(rep):
    os.makedirs(os.path.join(HERE, "..", "results"), exist_ok=True)
    stamp = rep["run_at"][:10]
    jp = os.path.join(HERE, "..", "results", f"audit-{stamp}.json")
    mp = os.path.join(HERE, "..", "results", f"audit-{stamp}.md")
    json.dump(rep, open(jp, "w"), indent=1)
    L = [f"# FMA catalog audit — {stamp}", "",
         f"Catalog: `{rep['ref']}` @ `{rep['sha']}` · {rep['counts']['apps']} apps "
         f"({rep['counts']['darwin']} macOS, {rep['counts']['windows']} Windows) · network checks: {'on' if rep['network'] else 'off'}", "",
         "| Check | Findings | Escapes |", "|---|---|---|"]
    for key, label, esc in [("duplicates", "1.2 Duplicate entries", "#53287"),
                            ("behind_upstream", "1.3 Behind upstream", "#52746 #55546"),
                            ("frozen_inputs", "1.3 Frozen inputs (ingest skips them silently)", "#55546"),
                            ("platform_divergence", "1.3 Windows behind macOS, winget has nothing newer", "#52746"),
                            ("url_dead", "1.4 Installer host no longer exists", "#55360"),
                            ("url_html_confirm_via_fleet", "1.4 URL returns 404 or a web page to curl (confirm by adding via Fleet)", "#55360"),
                            ("url_unreachable", "1.4 Installer URL unreachable after retry", "#55360"),
                            ("url_blocked_needs_manual_check", "1.4 URL refuses non-browser clients (check by hand)", "#55360"),
                            ("hash_problems", "1.4 SHA-256 mismatch / download failed", "#55360"),
                            ("script_missing", "1.5 Install/uninstall script missing", "#51577"),
                            ("version_shape", "1.6 Version shape / order", "#50968"),
                            ("version_went_down", "1.6 Version went down", "#50968 #55546"),
                            ("windows_user_scope", "1.7 Windows user-scope installers", "#50857 #51138")]:
        v = rep["checks"].get(key)
        L.append(f"| {label} | {'—' if v is None else len(v)} | {esc} |")
    L += ["", f"Hashless (`no_check`) apps: {len(rep['hashless'])} · "
          f"scripts that hardcode their version (expected, context only): {rep.get('scripts_hardcoding_version', 0)}"
          + (f" · freshness lookups that failed: {len(rep.get('freshness_errors', []))}" if rep['network'] else ""), ""]
    for key, items in rep["checks"].items():
        if not items:
            continue
        L += [f"## {key}", ""]
        for it in items[:60]:
            L.append("- " + ", ".join(f"{k}: {v}" for k, v in it.items() if k != "lines") +
                     (f" — `{it['lines'][0][:100]}`" if it.get("lines") else ""))
        if len(items) > 60:
            L.append(f"- … {len(items) - 60} more in the JSON")
        L.append("")
    open(mp, "w").write("\n".join(L))
    return jp, mp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--network", action="store_true")
    ap.add_argument("--hash-sample", type=int, default=0, help="max apps to download+hash this run")
    ap.add_argument("--history-days", type=int, default=7)
    a = ap.parse_args()
    if not a.no_fetch and a.ref.startswith("origin/"):
        subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", a.ref.split("/", 1)[1]], check=False)
    sha, apps, manifests, inputs = load_catalog(a.ref)
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "ref": a.ref, "sha": sha, "network": a.network,
           "counts": {"apps": len(apps), "darwin": sum(x["platform"] == "darwin" for x in apps),
                      "windows": sum(x["platform"] == "windows" for x in apps), "manifests": len(manifests), "inputs": len(inputs)},
           "hashless": hashless(manifests), "checks": {}}
    c = rep["checks"]
    c["duplicates"] = check_duplicates(apps)
    c["version_shape"] = check_versions(manifests)
    c["version_went_down"], rep["history_ref"] = check_version_regressions(a.ref, manifests, a.history_days)
    sf = check_scripts(manifests)
    # Hardcoded versions are expected since #52057 (the cron compares against the previous manifest's
    # script), so they're a count for context, not findings.
    c["script_missing"] = [x for x in sf if x["kind"].endswith("_missing")]
    rep["scripts_hardcoding_version"] = len([x for x in sf if "hardcodes" in x["kind"]])
    c["windows_user_scope"] = check_windows_scope(inputs)
    c["frozen_inputs"] = check_frozen(a.ref, inputs)
    c["missing_manifest"] = [{"slug": x["slug"]} for x in apps if x["slug"] not in manifests]
    if a.network:
        c["behind_upstream"], c["platform_divergence"], rep["freshness_errors"] = check_freshness(
            a.ref, apps, manifests, inputs, CFG["audit"]["freshness_lag_days"])
        urls = check_urls(manifests)
        for k in ("url_dead", "url_html_confirm_via_fleet", "url_unreachable", "url_blocked_needs_manual_check"):
            c[k] = [x for x in urls if x["kind"] == k]
        if a.hash_sample:
            c["hash_problems"], rep["hashed_today"] = check_hashes(manifests, a.hash_sample, CFG["audit"]["full_hash_rotation_days"])
    jp, mp = write_report(rep)
    print(open(mp).read().split("\n## ")[0])
    print(f"\nreport: {mp}\njson:   {jp}")


if __name__ == "__main__":
    main()
