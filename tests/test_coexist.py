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


class GenericBackoff(unittest.TestCase):
    def test_stuck_recently(self):
        now = datetime.datetime(2026, 11, 20, 22)
        led = {"db-browser-for-sqlite/windows@864": {"last_lifecycle": {"outcome": sample.PREEXISTING_STUCK + " (failed_uninstall)", "date": "2026-11-20T20:00:00"}},
               "db-browser-for-sqlite/windows@861": {"last_lifecycle": {"outcome": sample.PREEXISTING_STUCK, "date": "2026-11-20T20:00:00"}},
               "vlc/windows@864": {"last_lifecycle": {"outcome": "pass", "date": "2026-11-20T20:00:00"}},
               "old/windows@864": {"last_lifecycle": {"outcome": sample.PREEXISTING_STUCK, "date": "2026-11-10T00:00:00"}}}
        self.assertEqual(sample.stuck_recently(led, 864, now), {"db-browser-for-sqlite/windows"})


class AddErrors(unittest.TestCase):
    def test_variant_sibling(self):
        self.assertEqual(runner.variant_sibling("slack-arm64/windows"), "slack/windows")
        self.assertEqual(runner.variant_sibling("firefox@esr/darwin"), "firefox/darwin")
        self.assertIsNone(runner.variant_sibling("slack/windows"))

    def run_add(self, responses, apps):
        calls = []
        def api(method, path, body=None, timeout=120, tries=5):
            calls.append((method, path))
            if method == "GET":
                return 200, {"fleet_maintained_apps": apps}
            return responses.pop(0)
        saved = runner.api, runner.added_late, runner.time.sleep, runner.save_state
        runner.api, runner.added_late, runner.time.sleep, runner.save_state = api, (lambda s: None), (lambda x: None), (lambda *a: None)
        try:
            return runner.ensure_title(apps[0]["slug"], {"harness_titles": {}}, 1), calls
        finally:
            runner.api, runner.added_late, runner.time.sleep, runner.save_state = saved

    def test_variant_409_is_by_design(self):
        apps = [{"slug": "slack-arm64/windows", "id": 1}, {"slug": "slack/windows", "id": 2, "software_title_id": 9}]
        with self.assertRaises(runner.AddEnvironment) as e:
            self.run_add([(409, {"message": "Conflict"})], apps)
        self.assertIn("by design", str(e.exception))

    def test_409_without_sibling_in_fleet_is_a_finding(self):
        apps = [{"slug": "slack-arm64/windows", "id": 1}, {"slug": "slack/windows", "id": 2}]
        with self.assertRaises(RuntimeError) as e:
            self.run_add([(409, {"message": "Conflict"})], apps)
        self.assertNotIsInstance(e.exception, runner.AddEnvironment)

    def test_proxy_502_retried_once(self):
        apps = [{"slug": "rider/darwin", "id": 1}]
        (tid, added), calls = self.run_add([(502, {"message": "error code: 502"}), (200, {"software_title_id": 77})], apps)
        self.assertEqual((tid, added), (77, True))
        self.assertEqual(sum(1 for m, _ in calls if m == "POST"), 2)

    def test_sha_mismatch_stays_a_finding(self):
        apps = [{"slug": "megasync/windows", "id": 1}]
        with self.assertRaises(RuntimeError) as e:
            self.run_add([(500, {"message": "mismatch in maintained app SHA256 hash"})], apps)
        self.assertNotIsInstance(e.exception, runner.AddEnvironment)


class PresenceFallback(unittest.TestCase):
    def test_parses_osqueryd_json(self):
        saved = runner.api, runner.run_script
        runner.api = lambda *a, **k: (200, {"host": {"platform": "windows"}})
        try:
            runner.run_script = lambda h, b, timeout=600: {"exit_code": 0, "output": '[\n  {"1":"1"}\n]\n'}
            self.assertTrue(runner.present_by_script(1, "SELECT 1;"))
            runner.run_script = lambda h, b, timeout=600: {"exit_code": 0, "output": "[\n\n]"}
            self.assertFalse(runner.present_by_script(1, "SELECT 1;"))
            runner.run_script = lambda h, b, timeout=600: {"exit_code": 1, "output": "boom"}
            self.assertIsNone(runner.present_by_script(1, "SELECT 1;"))
        finally:
            runner.api, runner.run_script = saved


if __name__ == "__main__":
    unittest.main()
