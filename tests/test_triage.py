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
