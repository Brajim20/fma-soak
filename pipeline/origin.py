#!/usr/bin/env python3
"""Local FMA origin for the L1 update-pipeline suite (#55961, tasks 2.2-2.6).

Plays the role of the FMA manifest CDN and the vendors' download servers, so a test can "release" a new
version between cron passes and count every installer download.

  origin.py serve <state_dir> <port>      serve <state_dir>/catalog (/apps.json, /<slug>.json) and
                                           <state_dir>/installers (/installers/<file>); log installer hits
In Python (used by suite.py):
  Origin(state_dir, port).publish(slug, name, version, installer, sha="auto"|"no_check"|"<hex>",
                                  install_script=..., uninstall_script=..., url=None)
  Origin.hits(), Origin.reset_hits()

Manifests follow ee/maintained-apps/outputs/<slug>.json (versions[0] + refs).
"""
import hashlib, json, os, shutil, sys, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


class Origin:
    def __init__(self, state_dir, port):
        self.dir, self.port = os.path.abspath(state_dir), port
        self.catalog = os.path.join(self.dir, "catalog")
        self.installers = os.path.join(self.dir, "installers")
        os.makedirs(self.catalog, exist_ok=True)
        os.makedirs(self.installers, exist_ok=True)
        self.hits_path = os.path.join(self.dir, "hits.jsonl")

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def _apps(self):
        p = os.path.join(self.catalog, "apps.json")
        return json.load(open(p)) if os.path.exists(p) else {"version": 2, "apps": []}

    def publish(self, slug, name, version, installer, sha="auto", install_script="#!/bin/sh\necho install\n",
                uninstall_script="#!/bin/sh\necho uninstall\n", url=None, unique_identifier=None, patched=False):
        """Write (or replace) one app's manifest. `installer` is a local file copied under /installers/.
        sha: "auto" = real SHA-256 of the file, "no_check" = hashless, or an explicit (possibly wrong) hex."""
        base, platform = slug.split("/")
        fname = os.path.basename(installer)
        if os.path.abspath(installer) != os.path.join(self.installers, fname):
            shutil.copyfile(installer, os.path.join(self.installers, fname))
        if sha == "auto":
            sha = hashlib.sha256(open(os.path.join(self.installers, fname), "rb").read()).hexdigest()
        iref = hashlib.sha256(install_script.encode()).hexdigest()[:8]
        uref = hashlib.sha256(uninstall_script.encode()).hexdigest()[:8]
        uid = unique_identifier or (f"com.example.{base}" if platform == "darwin" else name)
        exists = (f"SELECT 1 FROM apps WHERE bundle_identifier = '{uid}';" if platform == "darwin"
                  else f"SELECT 1 FROM programs WHERE name = '{uid}';")
        queries = {"exists": exists}
        if patched:  # what real manifests carry and Fleet's patch policy runs (#51377)
            queries["patched"] = (
                f"SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM apps WHERE bundle_identifier = '{uid}' AND version_compare(bundle_short_version, '{version}') < 0);"
                if platform == "darwin" else
                f"SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM programs WHERE name = '{uid}' AND version_compare(version, '{version}') < 0);")
        manifest = {"versions": [{
            "version": version,
            "queries": queries,
            "installer_url": url or f"{self.base_url}/installers/{fname}",
            "install_script_ref": iref, "uninstall_script_ref": uref,
            "sha256": sha, "default_categories": ["Productivity"]}],
            "refs": {iref: install_script, uref: uninstall_script}}
        os.makedirs(os.path.join(self.catalog, base), exist_ok=True)
        json.dump(manifest, open(os.path.join(self.catalog, base, f"{platform}.json"), "w"), indent=1)
        apps = self._apps()
        apps["apps"] = [a for a in apps["apps"] if a["slug"] != slug] + [
            {"name": name, "slug": slug, "platform": platform, "unique_identifier": uid, "description": f"QA fixture {name}"}]
        json.dump(apps, open(os.path.join(self.catalog, "apps.json"), "w"), indent=1)
        return sha

    def hits(self):
        if not os.path.exists(self.hits_path):
            return []
        return [json.loads(l) for l in open(self.hits_path) if l.strip()]

    def reset_hits(self):
        open(self.hits_path, "w").close()


def serve(state_dir, port):
    o = Origin(state_dir, port)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _file(self, path, ctype, head):
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if head:
                return 0
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return size

        def _inside(self, root, rel):
            """The file under root that rel names, or None when rel climbs out of it (/../x.json)."""
            root = os.path.realpath(root)
            f = os.path.realpath(os.path.join(root, rel))
            return f if f.startswith(root + os.sep) else None

        def _handle(self, head=False):
            p = self.path.split("?")[0]
            if p.startswith("/installers/"):
                f = self._inside(o.installers, os.path.basename(p))
                rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "method": self.command, "file": os.path.basename(p),
                       "if_none_match": self.headers.get("If-None-Match"), "status": 404, "bytes": 0}
                if f and os.path.isfile(f):
                    rec["status"], rec["bytes"] = 200, self._file(f, "application/octet-stream", head)
                else:
                    self.send_response(404); self.end_headers()
                with open(o.hits_path, "a") as h:
                    h.write(json.dumps(rec) + "\n")
                return
            f = self._inside(o.catalog, p.lstrip("/"))
            if p.endswith(".json") and f and os.path.isfile(f):
                self._file(f, "application/json", head)
            else:
                self.send_response(404); self.end_headers()

        def do_GET(self):
            self._handle()

        def do_HEAD(self):
            self._handle(head=True)

    ThreadingHTTPServer(("127.0.0.1", int(port)), H).serve_forever()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "serve":
        serve(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
