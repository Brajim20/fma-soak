"""Tests for soak/titles.py sweep: what it keeps and what it removes, with the API faked. Run: python3 -m unittest discover tests"""
import json, os, sys, tempfile, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")  # runner reads a token at import; the API is faked below
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402
import titles  # noqa: E402

CATALOG = [
    {"slug": "zoom/darwin", "name": "Zoom", "software_title_id": 1},            # canary
    {"slug": "aptakube/windows", "name": "Aptakube", "software_title_id": 2},   # watcher resident
    {"slug": "slack/darwin", "name": "Slack", "software_title_id": 3},          # in the fleet before the soak
    {"slug": "dash/darwin", "name": "Dash", "software_title_id": 4},            # still on a host
    {"slug": "pycharm/darwin", "name": "PyCharm", "software_title_id": 5},      # queued
    {"slug": "bezel/darwin", "name": "Bezel", "software_title_id": 6},          # the soak's: removed
    {"slug": "warp/darwin", "name": "Warp", "software_title_id": 7},            # the soak's: removed
    {"slug": "fork/darwin", "name": "Fork"},                                    # not in the fleet
]


class Sweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        json.dump({"harness_titles": {"bezel/darwin": 6, "dash/darwin": 4}, "harness_installed": {"dash/darwin@636": True}},
                  open(os.path.join(self.tmp, "state-636.json"), "w"))
        self.deleted = []
        cfg = {"canaries": {"darwin": ["zoom/darwin"]}, "watcher": {"residents": {"windows": ["aptakube/windows"]}},
               "title_cleanup": {"keep_title_ids": [3]}}
        for p in (mock.patch.object(titles, "STATE", self.tmp), mock.patch.dict(runner.CFG, cfg),
                  mock.patch.object(runner, "busy_slugs", lambda: set()), mock.patch.object(runner, "api", self.api)):
            p.start()
            self.addCleanup(p.stop)

    def api(self, method, path, *a, **k):
        if method == "DELETE":
            self.deleted.append(int(path.split("/")[3]))
            return 204, {}
        if path.startswith("/hosts?"):
            return 200, {"hosts": [{"id": 636}]}
        if "/activities/upcoming" in path:
            return 200, {"activities": [{"details": {"software_title": "PyCharm"}}]}
        if path.startswith("/software/fleet_maintained_apps"):
            return 200, {"fleet_maintained_apps": CATALOG}
        raise AssertionError(path)

    def test_removes_only_the_soaks_titles(self):
        titles.sweep(dry=False)
        self.assertEqual(sorted(self.deleted), [6, 7])

    def test_forgets_removed_titles_in_state(self):
        titles.sweep(dry=False)
        s = json.load(open(os.path.join(self.tmp, "state-636.json")))
        self.assertEqual(s["harness_titles"], {"dash/darwin": 4})

    def test_dry_run_deletes_nothing(self):
        titles.sweep(dry=True)
        self.assertEqual(self.deleted, [])

    def test_unreadable_host_queue_removes_nothing(self):
        with mock.patch.object(runner, "api", lambda m, p, *a, **k: (502, {}) if "/activities/upcoming" in p else self.api(m, p)):
            with self.assertRaises(SystemExit):
                titles.sweep(dry=False)
        self.assertEqual(self.deleted, [])


if __name__ == "__main__":
    unittest.main()
