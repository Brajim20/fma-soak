import datetime, os, sys, tempfile, unittest
from unittest import mock
os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner, restarts

GD = ("Installing software... Failed Install exit code: 1610 (a previous Google Drive install or uninstall is "
      "waiting on a restart; restart the host, then install again)")


class Detect(unittest.TestCase):
    def test_google_drive_1610(self):
        self.assertIn("1610", runner.restart_needed({"first_install_output_tail": GD}))

    def test_3010(self):
        self.assertIn("3010", runner.restart_needed({"second_uninstall_output_tail": "Uninstall exit code: 3010"}))

    def test_preexisting_and_other_codes_ignored(self):
        self.assertIsNone(runner.restart_needed({"preexisting_uninstall_output_tail": GD,
                                                 "first_install_output_tail": "exit code: 13010 / exit code: 1603"}))


class Handle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = mock.patch.object(runner, "WS", self.tmp)
        self.p.start()
        self.cfg = mock.patch.dict(runner.CFG, {"restart": {"hosts": [863], "max_per_day": 2, "wait_minutes": 1}})
        self.cfg.start()

    def tearDown(self):
        self.p.stop(); self.cfg.stop()

    def test_unlisted_host_blocks(self):
        self.assertFalse(restarts.handle(861, "google-drive/windows", "windows"))
        self.assertIn("google-drive/windows", restarts.load(861)["blocked"])

    def test_daily_limit_blocks(self):
        today = restarts.now_utc().isoformat(timespec="seconds")
        restarts.save(863, {"history": [today, today], "blocked": {}})
        self.assertFalse(restarts.handle(863, "google-drive/windows", "windows"))
        self.assertIn("google-drive/windows", restarts.load(863)["blocked"])

    def test_restart_then_settled(self):
        import stuck
        ups = iter([86400, 300, 300])
        with mock.patch.object(restarts, "uptime", lambda h: next(ups)), mock.patch.object(stuck, "restart", lambda h: "sent"), \
                mock.patch.object(runner, "queue_idle", lambda h: True), mock.patch.object(restarts.time, "sleep"), \
                mock.patch.object(runner, "wait_online", lambda h: True):
            self.assertTrue(restarts.handle(863, "google-drive/windows", "windows"))
        s = restarts.load(863)
        self.assertEqual(len(s["history"]), 1)
        self.assertEqual(s["blocked"], {})

    def test_block_lifts_after_a_later_restart(self):
        restarts.save(863, {"history": [], "blocked": {"google-drive/windows": "2026-11-21T01:00:00+00:00"}})
        with mock.patch.object(runner, "api", lambda *a, **k: (200, {"host": {"last_restarted_at": "2026-11-20T00:00:00Z"}})):
            self.assertEqual(restarts.blocked(863), {"google-drive/windows"})
        with mock.patch.object(runner, "api", lambda *a, **k: (200, {"host": {"last_restarted_at": "2026-11-21T02:00:00Z"}})):
            self.assertEqual(restarts.blocked(863), set())


if __name__ == "__main__":
    unittest.main()
