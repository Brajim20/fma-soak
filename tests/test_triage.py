"""triage.json expiry rules in soak/digest.py. Run: python3 -m unittest discover tests"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import digest as D  # noqa: E402

REC = lambda started: {"started": started}


class Expired(unittest.TestCase):
    def setUp(self):
        self.closed = dict(D.CLOSED)
        D.CLOSED.clear()
        D.CLOSED[57001] = {"closedAt": "2026-11-05T12:00:00Z", "stateReason": "COMPLETED"}
        D.CLOSED[57002] = {"closedAt": "2026-11-05T12:00:00Z", "stateReason": "NOT_PLANNED"}

    def tearDown(self):
        D.CLOSED.clear(); D.CLOSED.update(self.closed)

    def test_filed_open_issue_never_expires(self):
        t = {"app": "x/darwin", "verdict": "filed #57003: something", "added": "2026-01-01"}
        self.assertFalse(D.expired(t, REC("2027-01-01T00:00:00")))

    def test_filed_closed_issue_expires_after_close(self):
        t = {"app": "x/darwin", "verdict": "filed #57001: something", "added": "2026-11-01"}
        self.assertFalse(D.expired(t, REC("2026-11-04T00:00:00")))
        self.assertTrue(D.expired(t, REC("2026-11-07T00:00:00")))

    def test_not_planned_close_keeps_hiding(self):
        t = {"app": "x/darwin", "verdict": "filed #57002: something", "added": "2026-11-01"}
        self.assertFalse(D.expired(t, REC("2026-11-07T00:00:00")))

    def test_all_filed_issues_must_be_closed(self):
        t = {"app": "x/darwin", "verdict": "filed #57001 and #57003: two bugs", "added": "2026-11-01"}
        self.assertFalse(D.expired(t, REC("2026-11-07T00:00:00")))

    def test_broad_verdict_expires_after_days(self):
        t = {"app": "x/darwin", "verdict": "environment: host restarted", "added": "2026-11-01"}
        self.assertFalse(D.expired(t, REC("2026-11-20T00:00:00")))
        self.assertTrue(D.expired(t, REC("2026-12-02T00:00:00")))

    def test_narrow_or_windowed_or_undated_never_expire(self):
        for t in ({"app": "x/darwin", "verdict": "environment", "added": "2026-01-01", "shape": "S3"},
                  {"app": "x/darwin", "verdict": "harness", "added": "2026-01-01", "before": "2026-11-01"},
                  {"app": "*@631", "verdict": "host stuck", "added": "2026-01-01"},
                  {"app": "x/darwin", "verdict": "environment"}):
            self.assertFalse(D.expired(t, REC("2027-01-01T00:00:00")), t)

    def test_recheck_sees_expired_entries(self):
        t = {"app": "x/darwin", "verdict": "filed #57001: something", "added": "2026-11-01"}
        D.TRIAGE.append(t)
        try:
            f, r = {"shape": "S3", "msg": "m"}, {"started": "2026-11-07T00:00:00"}
            self.assertIsNone(D.triage_match("x/darwin", f, r))
            self.assertIs(D.triage_match("x/darwin", f, r, live=False), t)
        finally:
            D.TRIAGE.remove(t)


if __name__ == "__main__":
    unittest.main()


class ArchPolicy(unittest.TestCase):
    """digest.arch_policy: x64 apps failing on Windows on ARM and ARM64-only apps failing on x64 are never filed."""
    F = {"shape": "install", "msg": "install failed"}

    def setUp(self):
        self.arch = dict(D.ARCH)
        self.triage = list(D.TRIAGE)
        D.ARCH.clear(); D.ARCH.update({"arm64": {863, 864, 629}, "x64": {861, 859, 862, 635}})
        D.TRIAGE.clear()

    def tearDown(self):
        D.ARCH.clear(); D.ARCH.update(self.arch)
        D.TRIAGE.clear(); D.TRIAGE.extend(self.triage)

    def rec(self, slug, host):
        return {"slug": slug, "host": host, "started": "2026-11-17T01:00:00"}

    def match(self, slug, host, **kw):
        r = self.rec(slug, host)
        return D.triage_match(f"{slug}@{host}", self.F, r, **kw)

    def test_x64_app_on_arm_host_is_excused(self):
        t = self.match("firefox@nightly/windows", 863)
        self.assertEqual(t["policy"], "arch")
        self.assertIn("x64 FMA on a Windows on ARM host", t["verdict"])

    def test_x64_app_on_x64_host_is_triaged(self):
        self.assertIsNone(self.match("firefox@nightly/windows", 861))

    def test_arm64_app_on_arm_host_is_triaged(self):
        self.assertIsNone(self.match("python-3.13-arm64/windows", 864))
        self.assertIsNone(self.match("firefox@nightly-arm64/windows", 629))

    def test_arm64_app_on_x64_host_is_excused(self):
        self.assertEqual(self.match("zoom-arm64/windows", 862)["policy"], "arch")

    def test_arm_in_name_but_not_suffix_is_x64(self):
        self.assertIsNone(self.match("garmin-basecamp/windows", 861))
        self.assertEqual(self.match("garmin-basecamp/windows", 864)["policy"], "arch")

    def test_unlisted_host_and_macos_untouched(self):
        self.assertIsNone(self.match("firefox/windows", 9001))  # pool runner
        self.assertIsNone(self.match("zoom-arm64/windows", 9001))
        self.assertIsNone(self.match("firefox/darwin", 863))

    def test_crosshost_skips_policy(self):
        self.assertIsNone(self.match("firefox@nightly/windows", 863, policy=False))

    def test_harness_findings_not_relabelled(self):
        r = self.rec("firefox/windows", 863)
        self.assertIsNone(D.triage_match("firefox/windows@863", {"shape": "harness", "msg": "x"}, r))
