"""Catalog name check (#56971 class): can each Windows FMA's exists query match the name Windows registers?

For every Windows FMA, takes the program name winget says the installer registers
(AppsAndFeaturesEntries.DisplayName in the newest installer manifest; else the locale manifest's PackageName,
which is weaker evidence) and evaluates the catalog's exists-query name conditions against it with SQLite LIKE
semantics (case-insensitive, % and _). An app whose registered name matches none of its patterns has an exists
query that never matches, so its patch policy always passes and outdated hosts are never upgraded (#56971).

  python3 names.py                # all Windows FMAs -> results/names-<date>.json/.md
  python3 names.py --only aws-cli/windows

It also lists collisions: an app's exists query that matches the name another app registers (#57051 shape).
"""
import argparse, datetime, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit as A

RAW = "https://raw.githubusercontent.com/microsoft/winget-pkgs/master/"


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=A.UA), timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def yaml_values(text, key):
    """Values of `key:` anywhere in a winget YAML (flat scan is enough for these manifests)."""
    out = []
    for m in re.finditer(rf"^\s*-?\s*{key}:\s*(.+?)\s*$", text or "", re.M):
        v = m.group(1).strip().strip("'\"")
        if v:
            out.append(v)
    return out


def registered_names(pkg_id):
    ver = A.winget_latest(pkg_id)
    if ver.startswith("ERR"):
        return {"error": ver}
    parts = pkg_id.split(".")
    base = f"{RAW}manifests/{parts[0][0].lower()}/" + "/".join(parts) + f"/{ver}/{pkg_id}"
    inst = fetch(base + ".installer.yaml")
    names = sorted(set(yaml_values(inst, "DisplayName")))
    pubs = sorted(set(yaml_values(inst, "Publisher")))
    src = "AppsAndFeaturesEntries"
    if not names:
        loc = fetch(base + ".locale.en-US.yaml")
        names, pubs, src = sorted(set(yaml_values(loc, "PackageName"))), sorted(set(yaml_values(loc, "Publisher"))), "PackageName"
    return {"winget_version": ver, "names": names, "publishers": pubs, "source": src}


def like(pattern, value):
    rx = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, value, re.I | re.S) is not None


def name_conditions(q):
    """(patterns, combine) from the exists query's name conditions; combine is all/any."""
    pats = [("like", p) for p in re.findall(r"\bname\s+LIKE\s+'([^']+)'", q, re.I)]
    pats += [("eq", p) for p in re.findall(r"\bname\s*=\s*'([^']+)'", q, re.I)]
    return pats, ("any" if re.search(r"\bOR\b", q, re.I) else "all")


def matches(q, name):
    pats, comb = name_conditions(q)
    if not pats:
        return None
    hits = [(like(p, name) if k == "like" else p.lower() == name.lower()) for k, p in pats]
    return any(hits) if comb == "any" else all(hits)


def check(slug, inp, man):
    q = man["versions"][0]["queries"]["exists"]
    reg = registered_names(inp["package_identifier"])
    row = {"slug": slug, "pkg": inp["package_identifier"], "fuzzy": bool(inp.get("fuzzy_match_name")), "exists": q, **reg}
    if reg.get("error") or not reg.get("names"):
        row["verdict"] = "unknown"
        return row
    results = {n: matches(q, n) for n in reg["names"]}
    row["matches"] = results
    # a name match still fails if the query pins a publisher Windows doesn't register (Wireshark: missing trailing '/')
    want_pub = re.findall(r"\bpublisher\s*=\s*'([^']+)'", q, re.I)
    if want_pub and reg["source"] == "AppsAndFeaturesEntries" and reg.get("publishers"):
        row["publisher_ok"] = any(p.lower() == w.lower() for p in reg["publishers"] for w in want_pub)
    if any(v is None for v in results.values()):
        row["verdict"] = "unknown"
    elif any(results.values()) and row.get("publisher_ok", True):
        row["verdict"] = "ok"
    else:
        row["verdict"] = "never_matches" if reg["source"] == "AppsAndFeaturesEntries" else "suspect"
    return row


def collisions(rows):
    """1.8 (#57051 shape, the two Druva apps): app A's exists query matches the name app B registers, so a host with
    B shows A as installed and A's patch policy judges B's version. Only names winget lists as registered
    (AppsAndFeaturesEntries) count; PackageName is too weak to call a collision."""
    out = []
    for a in rows:
        if not a.get("exists"):
            continue
        for b in rows:
            if b is a or b.get("source") != "AppsAndFeaturesEntries" or not b.get("names"):
                continue
            hit = [n for n in b["names"] if matches(a["exists"], n)]
            if hit:
                out.append({"query_of": a["slug"], "matches_app": b["slug"], "names": hit})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    a = ap.parse_args()
    inputs = {}
    for p in A.git("ls-tree", "-r", "--name-only", "origin/main", "ee/maintained-apps/inputs/winget").split():
        if p.endswith(".json") and "/scripts/" not in p:
            i = json.loads(A.git("show", f"origin/main:{p}"))
            inputs[i["slug"]] = i
    slugs = [a.only] if a.only else sorted(inputs)
    def one(s):
        b, plat = s.split("/")
        try:
            man = json.loads(A.git("show", f"origin/main:ee/maintained-apps/outputs/{b}/{plat}.json"))
        except Exception:
            return {"slug": s, "verdict": "unknown", "error": "no manifest"}
        return check(s, inputs[s], man)
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, slugs))
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("verdicts:", counts)
    coll = collisions(rows)
    print(f"collisions: {len(coll)} (an app's exists query matches another app's registered name)")
    for c in coll[:40]:
        print(f"  {c['query_of']:40} matches {c['matches_app']:40} {c['names']}")
    bad = [r for r in rows if r["verdict"] in ("never_matches", "suspect")]
    for r in bad:
        conds = [p for _, p in name_conditions(r["exists"])[0]]
        print(f"  {r['verdict']:14} {r['slug']:40} names={r['names']} | patterns={conds}")
    if not a.only:
        stamp = datetime.date.today().isoformat()
        out = os.path.join(HERE, "..", "results", f"names-{stamp}.json")
        json.dump({"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "counts": counts, "rows": rows,
                   "collisions": coll},
                  open(out, "w"), indent=1)
        print(out)


if __name__ == "__main__":
    main()
