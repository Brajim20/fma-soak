"""Tests for sample.py next_pick's config rerun list. Run: python3 -m unittest discover tests"""
import os, random, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")  # runner reads a token at import; nothing here calls the API
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402
import sample  # noqa: E402

CATALOG = ["bezel/darwin", "mist/darwin", "warp/darwin"]


class Rerun(unittest.TestCase):
    def setUp(self):
        cfg = {"rerun": {"since": "2026-11-07T11:15", "why": "clean run", "apps": ["mist/darwin", "bezel/darwin", "fork/darwin"]},
               "random_sample": {"changed_share": 0, "blind_share": 0}}
        for p in (mock.patch.dict(runner.CFG, cfg), mock.patch.object(sample, "excluded", lambda plat, host: set()),
                  mock.patch.object(sample, "new_apps", lambda *a: []), mock.patch.object(sample, "all_hosts", lambda plat: [631, 636])):
            p.start()
            self.addCleanup(p.stop)
        self.deck = {"order": ["warp/darwin"], "done": [], "round": 1, "started": "2026-11-01T00:00"}

    def pick(self, ledger):
        return sample.next_pick("darwin", self.deck, CATALOG, set(), ledger, random.Random(1), 631)

    def test_listed_app_goes_first(self):
        self.assertEqual(self.pick({}), ("mist/darwin", "rerun: clean run"))

    def test_a_run_since_on_any_host_counts(self):
        ledger = {"mist/darwin@636": {"last_lifecycle": {"date": "2026-11-07T12:00"}},
                  "bezel/darwin@631": {"last_lifecycle": {"date": "2026-11-07T09:00"}}}  # before `since`: still due
        self.assertEqual(self.pick(ledger)[0], "bezel/darwin")

    def test_apps_outside_this_hosts_catalog_are_left_to_their_host(self):
        ledger = {s + "@631": {"last_lifecycle": {"date": "2026-11-08"}} for s in ("mist/darwin", "bezel/darwin")}
        self.assertEqual(self.pick(ledger), ("warp/darwin", "deck round 1"))


if __name__ == "__main__":
    unittest.main()
