"""trends.py drift checks. Run: python3 -m unittest discover -s tests"""
import datetime, os, sys, unittest

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import trends as T  # noqa: E402

TODAY = datetime.date(2026, 11, 27)
ARCH = {"arm64": {863, 864}, "x64": {861, 859, 862}}


def rec(i, secs=None, outcome="pass", host=865, slug="app/darwin", **kw):
    r = {"slug": slug, "host": host, "outcome": outcome, "findings": [],
         "started": (datetime.datetime(2026, 11, 27) - datetime.timedelta(days=12 - i, hours=1)).isoformat()}
    if secs is not None:
        r["first_install_seconds"] = secs
    r.update(kw)
    return r


def cat(r):
    return {"pass": "pass", "findings": "findings"}.get(r["outcome"], "environment")


class Slower(unittest.TestCase):
    def test_flags_a_step_three_times_slower(self):
        recs = [rec(i, 30) for i in range(6)] + [rec(6, 200), rec(7, 220)]
        (slug, grp, label, was, now, n, hosts), = T.slower(recs, ARCH, TODAY)
        self.assertEqual((slug, grp, label, was, now), ("app/darwin", "darwin", "first install", 30, 210))

    def test_small_absolute_change_is_noise(self):
        recs = [rec(i, 10) for i in range(6)] + [rec(6, 60), rec(7, 70)]  # 6x, but only 55 s
        self.assertEqual(T.slower(recs, ARCH, TODAY), [])

    def test_failed_runs_dont_count(self):
        recs = [rec(i, 30) for i in range(6)] + [rec(6, 3600, outcome="findings"), rec(7, 3600, outcome="findings")]
        self.assertEqual(T.slower(recs, ARCH, TODAY), [])

    def test_too_little_history(self):
        recs = [rec(i, 30) for i in range(3)] + [rec(3, 300), rec(4, 300)]
        self.assertEqual(T.slower(recs, ARCH, TODAY), [])

    def test_arm_and_x64_are_compared_separately(self):
        # the same app is slower on ARM VMs: alternating hosts is not drift
        recs = [rec(i, 30, host=861, slug="app/windows") for i in range(6)]
        recs += [rec(6, 300, host=863, slug="app/windows"), rec(7, 300, host=864, slug="app/windows")]
        self.assertEqual(T.slower(recs, ARCH, TODAY), [])

    def test_stale_series_drops_out(self):
        recs = [rec(i, 30) for i in range(6)] + [rec(6, 300), rec(7, 300)]
        self.assertEqual(T.slower(recs, ARCH, TODAY + datetime.timedelta(days=60)), [])


class FailingMore(unittest.TestCase):
    def test_flags_new_failures_on_first_tries(self):
        recs = [rec(i) for i in range(8)] + [rec(8, outcome="findings"), rec(9), rec(10, outcome="findings"), rec(11)]
        (slug, grp, rf, rn, bf, bn, hosts), = T.failing_more(recs, cat, ARCH, TODAY)
        self.assertEqual((rf, rn, bf, bn), (2, 4, 0, 8))

    def test_already_failing_app_is_not_a_trend(self):
        recs = [rec(i, outcome="findings") for i in range(8)] + [rec(8 + i, outcome="findings") for i in range(4)]
        self.assertEqual(T.failing_more(recs, cat, ARCH, TODAY), [])

    def test_retries_and_environment_dont_count(self):
        recs = [rec(i) for i in range(8)] + [rec(8 + i, outcome="environment: host offline") for i in range(4)]
        recs += [rec(20, outcome="findings", retry_of="x"), rec(21, outcome="findings", retry_of="y")]
        self.assertEqual(T.failing_more(recs, cat, ARCH, TODAY), [])


class Section(unittest.TestCase):
    def test_lines_name_the_hosts(self):
        recs = [rec(i, 30) for i in range(6)] + [rec(6, 200, host=860), rec(7, 220)]
        (line,) = T.section(recs, cat, ARCH, TODAY)
        self.assertIn("first install takes 210s, up from 30s", line)
        self.assertIn("860, 865", line)


if __name__ == "__main__":
    unittest.main()
