"""Winget source health for Windows FMAs (audit 1.11, 1.12, 1.13): the catalog can be "up to date" with a winget
package that upstream stopped updating, and its queries can miss the publisher an install registers under.

  1.11 successor_package   the package's parent folder in microsoft/winget-pkgs is itself a package with newer
                           versions than ours (JetBrains.PyCharm.Professional -> JetBrains.PyCharm, #58816;
                           JetBrains.IntelliJIDEA.Ultimate -> JetBrains.IntelliJIDEA, #51801)
  1.12 dormant_source      the newest version folder was added more than `dormant_days` ago, and the macOS app of the
                           same name (paired by name, not slug) has a newer version (Tableau.Desktop, #58817). Without
                           a macOS counterpart an app is only counted as quiet past `dormant_days_alone`.
  1.13 publisher_drift     the winget manifests for the version we ship or older ones name a publisher the
                           exists/patched queries (input program_publisher) don't match (Tableau Software, LLC vs
                           Salesforce, Inc, #58817)

Every lookup goes through a Winget object, so tests replace it with fixtures (tests/test_winget_health.py).
"""
import datetime, json, re, subprocess, urllib.request
from functools import cmp_to_key

PUB_SUFFIX = re.compile(r"[,.]?\s*\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|s\.?r\.?o|s\.?a|ag|b\.?v|plc|pty)\b\.?", re.I)


def vparts(v):
    return [int(p) if p.isdigit() else p for p in re.findall(r"\d+|[a-z]+", str(v).split(",")[0].lower())]


def vcmp(a, b):
    pa, pb = vparts(a), vparts(b)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        return -1 if str(x) < str(y) else 1
    return (len(pa) > len(pb)) - (len(pa) < len(pb))


def is_version(name):
    """A version folder: "2026.3", "15" or "2.0-preview.9", not a child package like "64-bit"."""
    return bool(re.match(r"^\d+(\.|$)", name))


def same_release(a, b):
    """4.32 vs 4.32.0.1324: one is a prefix of the other."""
    pa, pb = vparts(a), vparts(b)
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


CHANNELS = {"esr", "lts", "beta", "dev", "nightly", "canary", "insiders", "preview", "eap"}


def pkg_path(pkg_id):
    parts = pkg_id.split(".")
    return f"manifests/{parts[0][0].lower()}/" + "/".join(parts)


def norm_pub(p):
    """'Tableau Software, LLC' -> 'tableau software'; 'JetBrains s.r.o.' -> 'jetbrains'; a leading 'The' and a
    trailing URL slash don't count."""
    p = PUB_SUFFIX.sub("", str(p or "")).lower()
    p = re.sub(r"^the\s+", "", re.sub(r"/+(?=\s|$)", "", p)).strip(" ,.")
    return re.sub(r"\s+", " ", p)


class Winget:
    """Live lookups against microsoft/winget-pkgs through gh (cached per run)."""

    def __init__(self, added_cache=None):
        """added_cache: path of a JSON file keeping each version folder's added date across runs (it never
        changes), so after the first run only new version folders cost an API call."""
        self._cache = {}
        self._added_path = added_cache
        try:
            self._added = json.load(open(added_cache)) if added_cache else {}
        except (OSError, ValueError):
            self._added = {}

    def save(self):
        if self._added_path:
            json.dump(self._added, open(self._added_path, "w"), indent=0, sort_keys=True)

    def _gh(self, path, jq):
        key = (path, jq)
        if key not in self._cache:
            r = subprocess.run(["gh", "api", path, "--jq", jq], capture_output=True, text=True)
            self._cache[key] = r.stdout if r.returncode == 0 else None
        return self._cache[key]

    def dirs(self, path):
        """Sub-folder names, or None when the path doesn't exist."""
        out = self._gh(f"repos/microsoft/winget-pkgs/contents/{path}", '.[] | select(.type=="dir") | .name')
        return None if out is None else out.split()

    def files(self, path):
        """Standard manifest names (<id>.installer.yaml, <id>.locale.en-US.yaml): read straight from
        raw.githubusercontent.com, so a full sweep stays far under the API rate limit."""
        pkg = ".".join(path.split("/")[2:-1])
        return [f"{pkg}.installer.yaml", f"{pkg}.locale.en-US.yaml"]

    def added(self, path):
        """Date (YYYY-MM-DD) of the oldest commit touching the path: when that version folder was added."""
        if path in self._added:
            return self._added[path]
        out = self._gh(f"repos/microsoft/winget-pkgs/commits?path={path}&per_page=100", ".[-1].commit.committer.date")
        d = out.strip()[:10] if out and out.strip() not in ("", "null") else None
        if d:
            self._added[path] = d
        return d

    def text(self, path):
        try:
            req = urllib.request.Request(f"https://raw.githubusercontent.com/microsoft/winget-pkgs/master/{path}",
                                         headers={"User-Agent": "fleet-qa-fma-audit"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            return None


def versions(w, path):
    """Version folders under a package path, newest first (child packages like Community/EAP left out)."""
    ds = w.dirs(path) or []
    return sorted((d for d in ds if is_version(d)), key=cmp_to_key(vcmp), reverse=True)


def publishers(w, path, version):
    """Publisher names the manifests for one version give: AppsAndFeaturesEntries (what Windows registers) first,
    then the default locale's Publisher."""
    out = []
    for f in w.files(f"{path}/{version}"):
        if f.endswith(".installer.yaml") or (".locale." in f and f.endswith(".yaml")):
            t = w.text(f"{path}/{version}/{f}") or ""
            for m in re.finditer(r"^\s*-?\s*Publisher:\s*['\"]?(.+?)['\"]?\s*$", t, re.M):
                kind = "registered" if f.endswith(".installer.yaml") else "locale"
                if (kind, m.group(1)) not in out:
                    out.append((kind, m.group(1)))
    return out


def counterparts(apps, manifests):
    """windows slug -> darwin slug for apps with the same name (apps.json), or the same slug base."""
    by_name = {}
    for a in apps:
        if a["slug"].endswith("/darwin"):
            by_name[re.sub(r"\W+", " ", a.get("name", "")).strip().lower()] = a["slug"]
    out = {}
    for a in apps:
        s = a["slug"]
        if not s.endswith("/windows"):
            continue
        base = s.split("/")[0]
        mac = f"{base}/darwin" if f"{base}/darwin" in manifests else by_name.get(re.sub(r"\W+", " ", a.get("name", "")).strip().lower())
        if mac:
            out[s] = mac
    return out


def check(apps, manifests, inputs, w, last_changed, today=None, dormant_days=180, dormant_days_alone=365, slugs=None,
          ignore=None):
    """(successor, dormant, drift, errors, quiet) for every Windows app with a winget package_identifier. `quiet` is
    context, not findings: no new winget version in `dormant_days_alone` and no macOS app to compare with.
    `ignore` ({"<check>": {slug: reason}}, config audit.winget_ignore) drops reviewed false alarms per check.
    `last_changed(slug)` gives the YYYY-MM-DD the catalog output last changed (audit.last_changed)."""
    today = today or datetime.date.today()
    pair = counterparts(apps, manifests)
    successor, dormant, drift, errors, quiet = [], [], [], [], []
    for a in apps:
        s = a["slug"]
        i = inputs.get(s, {})
        if not s.endswith("/windows") or not i.get("package_identifier") or (slugs and s not in slugs):
            continue
        pkg = i["package_identifier"]
        path = pkg_path(pkg)
        ours = (manifests.get(s, {}).get("versions") or [{}])[0].get("version", "")
        vs = versions(w, path)
        if not vs:
            errors.append({"slug": s, "package": pkg, "error": "no version folders in winget-pkgs"})
            continue
        # 1.11 successor: the parent folder is a package of its own with newer versions. A package pinned to a
        # release line (.NET.10, PostgreSQL.17, Firefox.ESR) is left alone when the slug says so; one whose slug
        # doesn't (TechSmith.Snagit.2026 as "snagit") is flagged when a newer line sits next to it.
        parts = pkg.split(".")
        if len(parts) >= 3:
            parent, last = ".".join(parts[:-1]), parts[-1]
            if last.lower() in CHANNELS:
                pass
            elif is_version(last):
                if last not in re.findall(r"\d+", s.split("/")[0]):
                    newer = sorted((d for d in (w.dirs(pkg_path(parent)) or []) if d.isdigit() and vcmp(d, last) > 0),
                                   key=cmp_to_key(vcmp))
                    if newer:
                        successor.append({"slug": s, "package": pkg, "ours": ours, "winget_latest": vs[0],
                                          "successor": f"{parent}.{newer[-1]}", "kind": "newer release line"})
            else:
                pv = versions(w, pkg_path(parent))
                if pv and vcmp(pv[0], max(ours, vs[0], key=cmp_to_key(vcmp))) > 0:
                    successor.append({"slug": s, "package": pkg, "ours": ours, "winget_latest": vs[0],
                                      "successor": parent, "successor_latest": pv[0], "kind": "parent package"})
        # 1.12 dormant: when the newest version folder was added
        added = w.added(f"{path}/{vs[0]}")
        if added:
            age = (today - datetime.date.fromisoformat(added)).days
            mac = pair.get(s)
            mac_changed = last_changed(mac) if mac else None
            mac_v = (manifests.get(mac, {}).get("versions") or [{}])[0].get("version", "") if mac else ""
            ahead = mac_v and vcmp(mac_v.lstrip("v"), ours) > 0 and not same_release(mac_v.lstrip("v"), ours)
            if age >= dormant_days and mac_changed and mac_changed > added and ahead:
                dormant.append({"slug": s, "package": pkg, "ours": ours, "winget_latest": vs[0], "latest_added": added,
                                "days": age, "macos": mac, "macos_version": manifests[mac]["versions"][0]["version"],
                                "macos_changed": mac_changed, "signal": "macOS moved since"})
            elif age >= dormant_days_alone and not mac:
                quiet.append({"slug": s, "package": pkg, "ours": ours, "winget_latest": vs[0], "latest_added": added,
                                "days": age, "signal": "no macOS counterpart"})
        # 1.13 publisher drift: what winget says vs what the queries match
        want = i.get("program_publisher")
        if want:
            seen = {}
            for v in [x for x in vs if vcmp(x, ours) <= 0][:3] or vs[:1]:
                for kind, p in publishers(w, path, v):
                    if norm_pub(p) != norm_pub(want):
                        seen.setdefault((kind, p), []).append(v)
            for (kind, p), on in seen.items():
                drift.append({"slug": s, "package": pkg, "query_publisher": want, "winget_publisher": p,
                              "source": kind, "versions": on})
    ign = ignore or {}
    keep = lambda name, xs: [x for x in xs if x["slug"] not in ign.get(name, {})]
    return keep("successor_package", successor), keep("dormant_source", dormant), keep("publisher_drift", drift), errors, quiet
