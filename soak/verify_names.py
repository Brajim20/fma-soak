"""Confirm a catalog name check finding (audit/names.py) on a real host: install the FMA through Fleet, read what
Windows actually registers, run the catalog's exists query, then uninstall and remove the title if the harness
added it. Prints one line per app and writes results/scenarios/<date>-names-confirm.json.

  python3 verify_names.py wireshark/windows:Wireshark arduino-ide/windows:Arduino ...
  (slug:keyword, the keyword finds the app in programs)
"""
import datetime, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner as R
import sample as S


def confirm(slug, keyword, host):
    state = R.load_state(host)
    exists_q = R.catalog_manifest(slug)["versions"][0]["queries"]["exists"]
    rec = {"slug": slug, "host": host, "build": R.build(), "exists": exists_q}
    try:
        tid, added = R.ensure_title(slug, state, host)
    except RuntimeError as e:
        rec["result"] = f"add failed: {e}"
        return rec
    rec["title_id"], rec["title_added"] = tid, added
    if R.present(host, exists_q) or R.api("POST", f"/hosts/{host}/query",
            {"query": f"SELECT 1 FROM programs WHERE name LIKE '%{keyword}%';"}, timeout=180)[1].get("rows"):
        rec["result"] = "skipped: already on the host"
        return rec
    key = f"{slug}@{host}"
    state["harness_installed"][key] = True; R.save_state(state, host)
    R.install(host, tid, rec, "install")
    for _ in range(4):
        st, d = R.api("POST", f"/hosts/{host}/query",
                      {"query": f"SELECT name, version, publisher FROM programs WHERE name LIKE '%{keyword}%';"}, timeout=180)
        if d.get("rows") is not None and not d.get("error"):
            break
    rec["registered"] = d.get("rows")
    rec["exists_matches"] = R.present(host, exists_q)
    sw = R.host_sw(host, tid) or {}
    rec["fleet_title_installed_versions"] = [v.get("version") for v in sw.get("installed_versions") or []]
    R.uninstall(host, tid, rec, "uninstall")
    gone = not R.api("POST", f"/hosts/{host}/query",
                     {"query": f"SELECT 1 FROM programs WHERE name LIKE '%{keyword}%';"}, timeout=180)[1].get("rows")
    rec["removed"] = gone
    if gone:
        state = R.load_state(host); state["harness_installed"].pop(key, None); R.save_state(state, host)
    rec["title_cleanup"] = S.remove_if_added(slug, host, {"title_added_by_harness": added}) if gone else "kept: app still on host"
    names = [r["name"] for r in rec["registered"] or []]
    rec["result"] = ("CONFIRMED: installed, registered as %s, exists query matches nothing" % names
                     if rec.get("install_status") == "installed" and names and rec["exists_matches"] is False else
                     "not confirmed: exists query matches" if rec["exists_matches"] else
                     f"inconclusive (install {rec.get('install_status')}, registered {names})")
    return rec


def main():
    host = R.CFG["hosts"]["windows"][0]
    out = []
    for arg in sys.argv[1:]:
        slug, kw = arg.split(":", 1)
        r = confirm(slug, kw, host)
        out.append(r)
        print(f"{slug}: {r['result']}", flush=True)
        os.makedirs(os.path.join(R.WS, "results", "scenarios"), exist_ok=True)
        json.dump(out, open(os.path.join(R.WS, "results", "scenarios",
                                         f"{datetime.date.today().isoformat()}-names-confirm.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
