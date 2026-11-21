import datetime, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "soak"))
import runner, sample

OD = "SELECT 1 FROM programs WHERE name = 'Microsoft OneDrive' AND publisher = 'Microsoft Corporation';"


class ScopeQuery(unittest.TestCase):
    def test_machine_checks_both_uninstall_roots(self):
        q = runner.scope_query(OD, "HKEY_LOCAL_MACHINE")
        self.assertIn(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\%", q)
        self.assertIn(r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\%", q)  # registry % is last-segment only
        self.assertIn("data = 'Microsoft OneDrive'", q)

    def test_per_user(self):
        self.assertIn(r"HKEY_USERS\%\Software", runner.scope_query(OD, "HKEY_USERS"))

    def test_like_and_quotes_kept(self):
        q = runner.scope_query("SELECT 1 FROM programs WHERE name LIKE 'Bob''s App %';", "HKEY_USERS")
        self.assertIn("data LIKE 'Bob''s App %'", q)

    def test_not_programs(self):
        self.assertIsNone(runner.scope_query("SELECT 1 FROM apps WHERE bundle_identifier = 'x';", "HKEY_USERS"))


class StuckBackoff(unittest.TestCase):
    def setUp(self):
        self.saved = sample.tiers, sample.tier_host, sample.last_cycled_index
        sample.tiers = lambda plat: [("twice_weekly", 84, ["onedrive/windows"])]
        sample.tier_host = lambda plat, s, h, t: 859
        sample.last_cycled_index = lambda l: {"onedrive/windows": "2026-10-30T20:13:43"}

    def tearDown(self):
        sample.tiers, sample.tier_host, sample.last_cycled_index = self.saved

    def due(self, outcome, date):
        led = {"onedrive/windows@859": {"last_lifecycle": {"outcome": outcome, "date": date}}}
        return sample.priority_due("windows", ["onedrive/windows"], led, 859, set(), (), datetime.datetime(2026, 11, 20, 22))

    def test_waits_a_period_after_stuck_skip(self):
        self.assertEqual(self.due(sample.PREEXISTING_STUCK + " (failed_uninstall)", "2026-11-20T21:42:20"), [])

    def test_due_again_after_the_period(self):
        self.assertTrue(self.due(sample.PREEXISTING_STUCK + " (failed_uninstall)", "2026-11-16T00:00:00"))

    def test_other_outcomes_unaffected(self):
        self.assertTrue(self.due("skipped: host low on disk", "2026-11-20T21:42:20"))


if __name__ == "__main__":
    unittest.main()
