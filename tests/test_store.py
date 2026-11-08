"""Tests for runner.store_problem (the stored-installer check) and store.py's alert. Run: python3 -m unittest discover tests"""
import os, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")  # runner reads a token at import; the network is faked below
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402
import store  # noqa: E402

VENDOR = "https://vendor.example/app.dmg"
TITLE = {"software_title": {"software_package": {"hash_sha256": "aaa"}}}


class StoreProblem(unittest.TestCase):
    def problem(self, stored, vendor, sha=None):
        sizes = lambda url, headers=None: vendor if url == VENDOR else stored
        with mock.patch.object(runner, "http_size", sizes), mock.patch.object(runner, "stored_sha256", lambda tid: sha), \
                mock.patch.object(runner, "api", lambda *a, **k: (200, TITLE)):
            return runner.store_problem(7, VENDOR)

    def test_empty_file_is_broken(self):
        self.assertIn("0 bytes", self.problem(0, None))

    def test_smaller_with_a_different_hash_is_broken(self):
        self.assertIn("1000 bytes of the vendor's 5000", self.problem(1000, 5000, sha="bbb"))

    def test_smaller_with_the_titles_hash_is_fine(self):  # the vendor shipped a smaller file at the same URL
        self.assertIsNone(self.problem(1000, 5000, sha="aaa"))

    def test_cant_tell_runs_the_cycle(self):
        self.assertIsNone(self.problem(None, 5000))     # the instance didn't say
        self.assertIsNone(self.problem(1000, None))     # the vendor didn't say
        self.assertIsNone(self.problem(1000, 5000))     # the download for the hash failed
        self.assertIsNone(self.problem(6000, 5000))     # bigger: a newer vendor file, not truncation


def rec(started, outcome, title=1):
    return {"slug": "zoom/darwin", "host": 631, "started": started, "outcome": outcome, "title_id": title}


class Alert(unittest.TestCase):
    BAD = runner.STORE_BROKEN + ": the instance serves this title's installer as 0 bytes"

    def test_alerts_once_then_recovers(self):
        st, lines, alert = store.check([rec("2026-11-08T01:00", "pass"), rec("2026-11-08T02:00", self.BAD)], {})
        self.assertTrue(alert)
        self.assertIn("0 bytes", lines[1])
        st, _, alert = store.check([rec("2026-11-08T03:00", self.BAD)], st)
        self.assertFalse(alert)  # already alerted
        st, lines, alert = store.check([rec("2026-11-08T04:00", "findings")], st)
        self.assertFalse(alert)
        self.assertIn("installer store OK again", lines[0])
        self.assertFalse(st["alerted"])

    def test_skips_dont_count_as_recovery(self):
        st, _, _ = store.check([rec("2026-11-08T02:00", self.BAD)], {})
        st, lines, _ = store.check([rec("2026-11-08T03:00", "skipped: held by the patch-when-closed scenario")], st)
        self.assertTrue(st["alerted"])


if __name__ == "__main__":
    unittest.main()
