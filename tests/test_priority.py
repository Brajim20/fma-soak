"""Tests for the priority tiers and weekly quarters (sample.py), the update path's due rules (runner.update_due,
update_host) and the titles kept for it (titles.py). Run: python3 -m unittest discover tests"""
import datetime, os, random, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")  # runner reads a token at import; nothing here calls the API
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402
import sample  # noqa: E402
import titles  # noqa: E402

FULL = [f"app{i}/darwin" for i in range(40)]
NOW = datetime.datetime(2026, 11, 18, 12, 0)  # a Wednesday; the week began Monday 2026-11-16
PRIORITY = {"slack_hours": 1, "grace_hours": 6,
            "tiers": [{"name": "twice_daily", "every_hours": 12, "apps": ["app0/darwin", "app1/darwin"]},
                      {"name": "daily", "every_hours": 24, "apps": ["app2/darwin", "app3/darwin"]},
                      {"name": "twice_weekly", "every_hours": 84, "apps": ["app4/darwin", "app0/windows"]}],
            "update": {"keep_tiers": ["twice_daily"], "new_build_tiers": ["twice_daily", "daily"], "every_days": 7}}


def ago(hours):
    return (NOW - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")


class Base(unittest.TestCase):
    hosts = [631, 636]
    up = (631, 636)

    def setUp(self):
        cfg = {"rerun": {}, "priority": PRIORITY, "quarters": {"count": 4, "epoch": "2026-11-16"},
               "random_sample": {"changed_share": 0, "blind_share": 0, "host_weights": {}}, "coverage": {}}
        for p in (mock.patch.dict(runner.CFG, cfg), mock.patch.object(sample, "excluded", lambda plat, host: set()),
                  mock.patch.object(sample, "new_apps", lambda *a: []), mock.patch.object(sample, "known_env_hosts", lambda s: set()),
                  mock.patch.object(sample, "pool_for", lambda plat: []), mock.patch.object(sample, "hosts_for", lambda plat: self.hosts),
                  mock.patch.object(sample, "is_up", lambda plat, h: h in self.up)):
            p.start()
            self.addCleanup(p.stop)

    def covered(self, hours):
        """Every app covered and last cycled `hours` before NOW."""
        return {s + "@636": {"first_cycled": "2026-11-01", "last_cycled": ago(hours)} for s in FULL}

    def pick(self, host, ledger, tried=()):
        return sample.next_pick("darwin", FULL, set(), ledger, random.Random(1), host, None, FULL, set(tried), NOW)

    def picks(self, ledger):
        """Everything each host would pick this pass, in order."""
        out = {}
        for h in self.hosts:
            tried, out[h] = set(), []
            while p := self.pick(h, ledger, tried):
                out[h].append(p); tried.add(p[0])
        return out


class Priority(Base):
    def test_overdue_priority_apps_come_first_most_overdue_first(self):
        ledger = self.covered(1)
        ledger["app0/darwin@636"]["last_cycled"] = ago(13)  # 13/12 overdue
        ledger["app2/darwin@636"]["last_cycled"] = ago(40)  # 40/24: the most overdue
        firsts = [p for ps in self.picks(ledger).values() for p in ps if p[1].startswith("priority")]
        self.assertEqual(sorted(s for s, _ in firsts), ["app0/darwin", "app2/darwin"])
        host = sample.tier_host("darwin", "app2/darwin", 24, NOW.timestamp())
        self.assertEqual(self.pick(host, ledger)[0], "app2/darwin")
        self.assertEqual(self.pick(host, ledger)[1], "priority daily: last cycled 40 h ago")

    def test_not_due_until_its_interval_less_the_slack(self):
        ledger = self.covered(10.5)  # twice_daily is due from 11 h
        self.assertEqual(sample.priority_due("darwin", FULL, ledger, 631, set(), set(), NOW) +
                         sample.priority_due("darwin", FULL, ledger, 636, set(), set(), NOW), [])
        ledger = self.covered(11.2)
        due = {s for h in self.hosts for s, _ in sample.priority_due("darwin", FULL, ledger, h, set(), set(), NOW)}
        self.assertEqual(due, {"app0/darwin", "app1/darwin"})

    def test_each_due_app_goes_to_exactly_one_host(self):
        ledger = self.covered(100)
        takers = {}
        for h in self.hosts:
            for s, _ in sample.priority_due("darwin", FULL, ledger, h, set(), set(), NOW):
                takers.setdefault(s, []).append(h)
        self.assertEqual(sorted(takers), ["app0/darwin", "app1/darwin", "app2/darwin", "app3/darwin", "app4/darwin"])
        self.assertTrue(all(len(v) == 1 for v in takers.values()), takers)

    def test_the_host_rotates_between_periods(self):
        seen = {sample.tier_host("darwin", "app0/darwin", 12, NOW.timestamp() + k * 12 * 3600) for k in range(12)}
        self.assertEqual(seen, {631, 636})

    def test_an_offline_hosts_priority_app_goes_to_the_other(self):
        self.up = (636,)
        ledger = self.covered(100)
        self.assertEqual(sample.priority_due("darwin", FULL, ledger, 631, set(), set(), NOW), [])
        self.assertEqual(len(sample.priority_due("darwin", FULL, ledger, 636, set(), set(), NOW)), 5)

    def test_another_platforms_apps_are_left_out(self):
        self.assertNotIn("app0/windows", sample.priority_apps("darwin"))

    def test_schedule_reports_on_time_and_overdue(self):
        apps = self.covered(1)
        apps["app2/darwin@636"]["last_cycled"] = ago(31)  # daily: 24 h + 6 h grace
        del apps["app4/darwin@636"]
        out = sample.schedule("darwin", FULL, apps, set(), NOW)
        t = {x["name"]: x for x in out["priority"]}
        self.assertEqual((t["twice_daily"]["on_time"], t["twice_daily"]["overdue"]), (2, {}))
        self.assertEqual(t["daily"]["overdue"], {"app2/darwin": 31})
        self.assertEqual(t["twice_weekly"]["overdue"], {"app4/darwin": None})


class Quarter(Base):
    def test_weeks_cycle_through_the_quarters(self):
        weeks = [sample.week_quarter(datetime.datetime(2026, 11, 16) + datetime.timedelta(days=7 * k)) for k in range(5)]
        self.assertEqual([q for q, _ in weeks], [0, 1, 2, 3, 0])
        self.assertEqual(sample.week_quarter(NOW), (0, "2026-11-16"))
        self.assertEqual(sample.week_quarter(datetime.datetime(2026, 11, 22, 23, 59)), (0, "2026-11-16"))
        self.assertEqual(sample.week_quarter(datetime.datetime(2026, 11, 9)), (3, "2026-11-09"))  # before the epoch

    def test_every_non_priority_app_in_exactly_one_quarter_and_quarters_roughly_even(self):
        apps = [f"x{i}/darwin" for i in range(1000)]
        counts = [sum(1 for s in apps if sample.quarter_of(s) == q) for q in range(4)]
        self.assertEqual(sum(counts), 1000)
        self.assertTrue(all(200 < c < 300 for c in counts), counts)

    def test_a_pass_takes_this_weeks_quarter_once_and_skips_priority_apps(self):
        ledger = self.covered(24 * 5)  # last week; priority apps cycled an hour ago, so only the quarter is due
        for s in sample.priority_apps("darwin"):
            ledger[s + "@636"]["last_cycled"] = ago(1)
        picked = [p for ps in self.picks(ledger).values() for p in ps]
        want = {s for s in FULL if sample.quarter_of(s) == 0} - sample.priority_apps("darwin")
        self.assertEqual(sorted(s for s, _ in picked), sorted(want))
        self.assertTrue(all(w == "quarter 1 (week of 2026-11-16)" for _, w in picked))

    def test_cycled_since_monday_is_done_for_the_week(self):
        ledger = self.covered(24 * 2)  # Monday noon
        for s in sample.priority_apps("darwin"):
            ledger[s + "@636"]["last_cycled"] = ago(1)
        self.assertEqual(self.picks(ledger), {631: [], 636: []})

    def test_schedule_counts_the_quarter(self):
        apps = self.covered(24 * 5)
        mine = sorted(s for s in FULL if sample.quarter_of(s) == 0 and s not in sample.priority_apps("darwin"))
        apps[mine[0] + "@636"]["last_cycled"] = ago(2)
        q = sample.schedule("darwin", FULL, apps, set(), NOW)["quarter"]
        self.assertEqual((q["index"], q["of"], q["week_start"], q["apps"], q["done"]), (1, 4, "2026-11-16", len(mine), 1))


class UpdateDue(unittest.TestCase):
    def setUp(self):
        cfg = {"priority": PRIORITY, "known_environment": {"app0/windows@629": "x64 only"}, "hosts": {"windows": [629, 635], "darwin": [631]}}
        p = mock.patch.dict(runner.CFG, cfg)
        p.start()
        self.addCleanup(p.stop)

    def last(self, hours, build="b1", tested="2.0"):
        return {"started": ago(hours), "build": build, "cached_versions": ["1.0", tested]}

    def due(self, slug, last, newest="2.0", build="b1"):
        return runner.update_due(slug, newest, last, build, NOW)

    def test_priority_app_same_version_same_build_is_not_due(self):
        self.assertIsNone(self.due("app0/darwin", self.last(30)))

    def test_new_release_makes_it_due(self):
        self.assertEqual(self.due("app0/darwin", self.last(1), newest="2.1"), "new version 2.1 (last run upgraded to 2.0)")

    def test_new_build_only_for_the_listed_tiers(self):
        self.assertEqual(self.due("app2/darwin", self.last(1), build="b2"), "new Fleet build b2")
        self.assertIsNone(self.due("app4/darwin", self.last(1), build="b2"))

    def test_weekly_even_when_nothing_changed(self):
        self.assertIsNone(self.due("app4/darwin", self.last(24 * 6)))
        self.assertTrue(self.due("app4/darwin", self.last(24 * 7)).startswith("weekly"))

    def test_never_run_is_due(self):
        self.assertEqual(self.due("app4/darwin", None), "never run on this host")

    def test_other_titles_keep_running_daily_and_on_a_new_build(self):
        self.assertIsNone(self.due("canary/darwin", self.last(10)))
        self.assertEqual(self.due("canary/darwin", self.last(21)), "last run 21 h ago")
        self.assertEqual(self.due("canary/darwin", self.last(1), build="b2"), "new Fleet build b2")

    def test_a_priority_app_the_primary_cant_run_moves_to_another_host(self):
        self.assertEqual(runner.update_host("windows", "app0/windows"), 635)
        self.assertEqual(runner.update_host("windows", "plain/windows"), 629)

    def test_a_non_priority_app_the_primary_cant_run_is_not_moved(self):
        with mock.patch.dict(runner.CFG["known_environment"], {"canary/windows@629": "x64 only"}):
            self.assertIsNone(runner.update_host("windows", "canary/windows"))


class KeptTitles(unittest.TestCase):
    def test_kept_tiers_titles_stay(self):
        with mock.patch.dict(runner.CFG, {"priority": PRIORITY, "canaries": {}, "watcher": {}}), \
                mock.patch.object(runner, "busy_slugs", lambda: set()), mock.patch.object(titles.glob, "glob", lambda p: []):
            keep = titles.kept_slugs()
        self.assertEqual(keep.get("app0/darwin"), "priority twice_daily (update path)")
        self.assertNotIn("app2/darwin", keep)


if __name__ == "__main__":
    unittest.main()
