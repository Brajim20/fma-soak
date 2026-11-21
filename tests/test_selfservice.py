import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "soak"))
import runner


class Due(unittest.TestCase):
    def test_share_bounds(self):
        saved = runner.CFG.get("self_service")
        try:
            runner.CFG["self_service"] = {"share": 0}
            self.assertFalse(any(runner.self_service_due(f"a{i}/darwin", "2026-11-21") for i in range(50)))
            runner.CFG["self_service"] = {"share": 1}
            self.assertTrue(all(runner.self_service_due(f"a{i}/darwin", "2026-11-21") for i in range(50)))
            runner.CFG["self_service"] = {"share": 0.25}
            n = sum(runner.self_service_due(f"a{i}/darwin", "2026-11-21") for i in range(400))
            self.assertTrue(60 < n < 140, n)
            self.assertEqual(runner.self_service_due("x/darwin", "2026-11-21"), runner.self_service_due("x/darwin", "2026-11-21"))
        finally:
            runner.CFG["self_service"] = saved


class Request(unittest.TestCase):
    def test_token_stays_on_host_and_status_parsed(self):
        sent = {}
        saved = runner.run_script
        runner.run_script = lambda host, body, timeout=0: sent.update(body=body) or {"output": "SS_STATUS=202 "}
        try:
            self.assertEqual(runner.self_service_request(1, 42, "darwin"), (202, ""))
        finally:
            runner.run_script = saved
        self.assertIn("/opt/orbit/identifier", sent["body"])
        self.assertIn("/api/latest/fleet/device/$T/software/install/42", sent["body"])


if __name__ == "__main__":
    unittest.main()
