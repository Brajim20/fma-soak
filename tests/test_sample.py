"""Tests for sample.py next_pick: the config rerun list, catch-up and the week's quarter. Run: python3 -m unittest discover tests"""
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
               "random_sample": {"changed_share": 0, "blind_share": 0}, "priority": {}}
        for p in (mock.patch.dict(runner.CFG, cfg), mock.patch.object(sample, "excluded", lambda plat, host: set()),
                  mock.patch.object(sample, "new_apps", lambda *a: []), mock.patch.object(sample, "all_hosts", lambda plat: [631, 636]),
                  mock.patch.object(sample, "quarter_of", lambda s: 0), mock.patch.object(sample, "week_quarter", lambda now=None: (0, "2026-11-09")),
                  mock.patch.object(sample, "catch_up_host", lambda plat, s: 631)):
            p.start()
            self.addCleanup(p.stop)

    def pick(self, ledger):
        return sample.next_pick("darwin", CATALOG, set(), ledger, random.Random(1), 631)

    def test_listed_app_goes_first(self):
        self.assertEqual(self.pick({}), ("mist/darwin", "rerun: clean run"))

    def test_a_run_since_on_any_host_counts(self):
        ledger = {"mist/darwin@636": {"last_lifecycle": {"date": "2026-11-07T12:00"}},
                  "bezel/darwin@631": {"last_lifecycle": {"date": "2026-11-07T09:00"}}}  # before `since`: still due
        self.assertEqual(self.pick(ledger)[0], "bezel/darwin")

    def test_apps_outside_this_hosts_catalog_are_left_to_their_host(self):
        ledger = {s + "@631": {"last_lifecycle": {"date": "2026-11-08"}, "first_cycled": "2026-11-08", "last_cycled": "2026-11-09T01:00"}
                  for s in ("mist/darwin", "bezel/darwin")}
        ledger["warp/darwin@636"] = {"first_cycled": "2026-10-31", "last_cycled": "2026-10-31"}  # before this week: due
        self.assertEqual(self.pick(ledger), ("warp/darwin", "quarter 1 (week of 2026-11-09)"))


FULL = [f"app{i}/darwin" for i in range(30)]


class CatchUp(unittest.TestCase):
    """Uncovered apps come before repeats, and each has an owner and a backup host."""
    def setUp(self):
        cfg = {"rerun": {}, "random_sample": {"changed_share": 0, "blind_share": 0, "host_weights": {}}, "priority": {},
               "coverage": {"invalid_windows": [{"after": "2026-11-06T15:00", "before": "2026-11-07T11:15"}]}}
        for p in (mock.patch.dict(runner.CFG, cfg), mock.patch.object(sample, "excluded", lambda plat, host: set()),
                  mock.patch.object(sample, "new_apps", lambda *a: []), mock.patch.object(sample, "known_env_hosts", lambda s: set()),
                  mock.patch.object(sample, "pool_for", lambda plat: []), mock.patch.object(sample, "hosts_for", lambda plat: [631, 636, 624]),
                  mock.patch.object(sample, "is_up", lambda plat, h: h != 624),  # 624 offline
                  mock.patch.object(sample, "quarter_of", lambda s: 0), mock.patch.object(sample, "week_quarter", lambda now=None: (0, "2026-11-09"))):
            p.start()
            self.addCleanup(p.stop)

    def pick(self, host, ledger, tried=()):
        mine = sample.shard("darwin", FULL, host)
        return sample.next_pick("darwin", mine, set(), ledger, random.Random(1), host, None, FULL, set(tried))

    def test_an_offline_hosts_share_is_picked_up_by_its_backups(self):
        covered = {s + "@631": {"first_cycled": "2026-11-01"} for s in FULL if sample.owner("darwin", s) != 624}
        theirs = [s for s in FULL if sample.owner("darwin", s) == 624]
        self.assertTrue(theirs)
        picked = set()
        for h in (631, 636):
            tried = set()
            while (p := self.pick(h, covered, tried)) and p[1].startswith("catch-up"):
                picked.add(p[0]); tried.add(p[0])
        self.assertEqual(picked, set(theirs))

    def test_uncovered_comes_before_a_quarter_repeat(self):
        s = next(x for x in FULL if sample.catch_up_host("darwin", x) == 631)
        ledger = {x + "@636": {"first_cycled": "2026-11-01"} for x in FULL if x != s}
        self.assertEqual(self.pick(631, ledger), (s, "catch-up: not covered on any host yet"))

    def test_tried_once_per_pass(self):
        s = next(x for x in FULL if sample.catch_up_host("darwin", x) == 631)
        ledger = {x + "@636": {"first_cycled": "2026-11-01"} for x in FULL if x != s}
        self.assertTrue(self.pick(631, ledger, {s})[1].startswith("quarter"))

    def test_each_uncovered_app_goes_to_exactly_one_online_host(self):
        takers = {s: [h for h in (631, 636, 624) if s in sample.catch_up("darwin", FULL, {}, h, set(), set())] for s in FULL}
        self.assertTrue(all(len(v) == 1 and v[0] != 624 for v in takers.values()), takers)
        self.assertEqual({v[0] for v in takers.values()}, {631, 636})

    def test_a_slug_with_an_at_sign_counts_as_covered(self):
        full = FULL + ["firefox@esr/darwin"]
        ledger = {x + "@636": {"first_cycled": "2026-11-01"} for x in full}
        host = sample.catch_up_host("darwin", "firefox@esr/darwin")
        self.assertNotIn("firefox@esr/darwin", sample.catch_up("darwin", full, ledger, host, set(), set()))

    def test_every_app_covered_means_the_weeks_quarter(self):
        ledger = {x + "@636": {"first_cycled": "2026-11-01", "last_cycled": "2026-11-01"} for x in FULL}
        self.assertTrue(self.pick(631, ledger)[1].startswith("quarter"))

    def test_quarter_done_this_week_means_nothing_left(self):
        ledger = {x + "@636": {"first_cycled": "2026-11-01", "last_cycled": "2026-11-10T08:00"} for x in FULL}
        self.assertIsNone(self.pick(631, ledger))

    def test_failure_inside_an_invalid_window_is_not_coverage(self):
        self.assertFalse(sample.counts_as_cycle("findings", "2026-11-06T20:00"))
        self.assertTrue(sample.counts_as_cycle("pass", "2026-11-06T20:00"))
        self.assertTrue(sample.counts_as_cycle("findings", "2026-11-07T12:00"))
        self.assertFalse(sample.counts_as_cycle("skipped: app already on host", "2026-11-08T01:00"))


if __name__ == "__main__":
    unittest.main()
