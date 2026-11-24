"""rc_report baseline selection. Run: python3 -m unittest discover -s tests"""
import os, sys, unittest

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import rc_report as RC  # noqa: E402


def run(outcome="findings", *shapes):
    return {"outcome": outcome, "findings": [{"shape": s, "msg": s} for s in shapes]}


class Baseline(unittest.TestCase):
    def test_add_failure_is_not_a_tested_run(self):
        self.assertFalse(RC.tested(run("findings", "add")))
        self.assertTrue(RC.tested(run("findings", "S1")))
        self.assertTrue(RC.tested(run("pass")))
        self.assertFalse(RC.tested(run("skipped: one version")))

    def test_last_run_an_add_failure_falls_back_to_the_tested_one(self):
        # chatgpt/darwin@860 on 4.94: S1/S6/S3 run, then an add failure; 4.95's S1/S3 aren't new
        cat, shapes = RC.baseline_of([run("findings", "S1", "S6", "S3"), run("findings", "add")])
        self.assertEqual(cat, "findings")
        self.assertTrue({"S1", "S3"} <= shapes)

    def test_shapes_union_over_tested_runs(self):
        _, shapes = RC.baseline_of([run("findings", "S1"), run("findings", "S3"), run("pass")])
        self.assertEqual(shapes, {"S1", "S3"})

    def test_newest_tested_run_sets_the_category(self):
        self.assertEqual(RC.baseline_of([run("findings", "S1"), run("pass")])[0], "pass")

    def test_never_tested_on_baseline_is_not_compared(self):
        self.assertIsNone(RC.baseline_of([run("findings", "add"), run("error: boom")]))



class Cause(unittest.TestCase):
    def rec(self, version="1.0", cid="aaa", *findings):
        return {"catalog_version": version, "catalog_id": cid,
                "findings": [{"shape": s, "msg": m} for s, m in findings]}

    def test_hash_mismatch_add_is_catalog(self):
        b = self.rec("1.3.9", "x", ("add", "add failed 500: mismatch in maintained app SHA256 hash"))
        self.assertIn("hash", RC.cause(self.rec("1.3.9", "x"), b))

    def test_other_add_failure_on_same_catalog_is_the_build(self):
        self.assertIsNone(RC.cause(self.rec(), self.rec("1.0", "aaa", ("add", "add failed 500: boom"))))

    def test_version_change_is_catalog(self):
        self.assertEqual(RC.cause(self.rec("26.1111.21308"), self.rec("26.1111.71025")), "catalog: 26.1111.21308 -> 26.1111.71025")

    def test_script_change_same_version_is_catalog(self):
        self.assertIn("manifest changed", RC.cause(self.rec("1.0", "aaa"), self.rec("1.0", "bbb")))

    def test_same_catalog_is_the_build(self):
        self.assertIsNone(RC.cause(self.rec(), self.rec("1.0", "aaa", ("S1", "x"))))

    def test_old_records_without_fingerprint_fall_back_to_version(self):
        a, b = self.rec("1.0", None), self.rec("1.0", "bbb")
        self.assertIsNone(RC.cause(a, b))


class NewCheck(unittest.TestCase):
    CHECKS = [{"match": "event log has errors", "since": "2026-11-21T22:46:01", "what": "event log"},
              {"match": r"self-service changed by (pinning|setting Latest)", "since": "2026-11-21T22:46:01", "what": "ss"}]

    def f(self, shape, msg):
        return {"shape": shape, "msg": msg}

    def test_finding_from_a_check_newer_than_the_baseline(self):
        # power-automate/windows@862: baseline pass 11-17, the event-log scan went live 11-21
        base = [{"started": "2026-11-17T06:38:00"}]
        self.assertEqual(RC.check_after_baseline([self.f("S2", "the Application event log has errors naming the app: x")], base, self.CHECKS), "event log")

    def test_any_older_finding_keeps_it_a_suspect(self):
        base = [{"started": "2026-11-17T06:38:00"}]
        fs = [self.f("S4", "self-service changed by pinning (#59238): True -> False"), self.f("S1", "inventory never reported")]
        self.assertIsNone(RC.check_after_baseline(fs, base, self.CHECKS))

    def test_baseline_that_ran_after_the_check_had_its_chance(self):
        base = [{"started": "2026-11-17T06:38:00"}, {"started": "2026-11-22T10:00:00"}]
        self.assertIsNone(RC.check_after_baseline([self.f("S2", "the Application event log has errors")], base, self.CHECKS))


class SameBuild(unittest.TestCase):
    def test_earlier_pass_on_the_current_build(self):
        # slack/darwin@858: passed on 5790b6c 11-17, S1 on the same build from 11-20 (858's inventory stopped)
        p, x = {"started": "2026-11-17T21:59:00", "outcome": "pass", "findings": []}, \
            {"started": "2026-11-20T00:55:00", "outcome": "findings", "findings": [{"shape": "S1", "msg": "m"}]}
        self.assertEqual(RC.same_build_pass([p, x], x), "2026-11-17T21:59:00")

    def test_no_earlier_pass(self):
        x = {"started": "2026-11-20T00:55:00", "outcome": "findings", "findings": [{"shape": "S1", "msg": "m"}]}
        flaky = {"started": "2026-11-18T00:00:00", "outcome": "flaky", "findings": []}
        self.assertIsNone(RC.same_build_pass([flaky, x], x))


CFG = {"arch": {"windows": {"arm64": [863, 864], "x64": [861, 859, 862]}}}
cpu_win = lambda h: RC.cpu(h, "windows", CFG)  # noqa: E731


def rec(host, outcome, *shapes, cid="c1"):
    return {"host": host, "outcome": outcome, "catalog_id": cid, "findings": [{"shape": s, "msg": s} for s in shapes]}


class Elsewhere(unittest.TestCase):
    K = ("python-3.13-arm64/windows", 864, "lifecycle")

    def seen(self, b, base):
        return RC.seen_elsewhere_on_baseline(b, b["findings"], self.K, {(self.K[0], self.K[2]): base}, cpu_win)

    def test_baseline_showed_it_on_another_host_with_the_same_cpu(self):
        # 4.94: 864 failed 1618 before inventory, 863 (also arm64) showed the S1
        self.assertEqual(self.seen(rec(864, "findings", "S1"), [rec(864, "findings", "install"), rec(863, "findings", "S1")]), [863])

    def test_other_cpu_does_not_count(self):
        self.assertEqual(self.seen(rec(864, "findings", "S1"), [rec(861, "findings", "S1")]), [])

    def test_every_finding_must_be_there(self):
        self.assertEqual(self.seen(rec(864, "findings", "S1", "S3"), [rec(863, "findings", "S1")]), [])

    def test_untested_baseline_run_does_not_count(self):
        self.assertEqual(self.seen(rec(864, "findings", "add"), [rec(863, "findings", "add")]), [])

    def test_generic_install_message_needs_the_same_installer_output(self):
        # adobe-creative-cloud: 'first install ended as failed_install' matches only with the same stub exit code
        f = {"shape": "install", "msg": "first install ended as failed_install"}
        b = {"host": 862, "outcome": "findings", "findings": [f], "first_install_output_tail": "Failed\nStub installer exited with code 206 after 40s\n"}
        same = dict(b, host=861, first_install_output_tail="x\nStub installer exited with code 206 after 40s")
        other = dict(b, host=859, first_install_output_tail="x\nStub installer exited with code 1 after 12s")
        k = ("adobe-creative-cloud/windows", 862, "lifecycle")
        self.assertEqual(RC.seen_elsewhere_on_baseline(b, [f], k, {(k[0], k[2]): [same, other]}, cpu_win), [861])

    def test_generic_uninstall_message_needs_the_same_api_error(self):
        f = {"shape": "S3", "msg": "first uninstall: app still on host (status None)"}
        b = {"host": 865, "outcome": "findings", "findings": [f], "first_uninstall_error": "Bad request"}
        k = ("wezterm/darwin", 865, "lifecycle")
        cpu_mac = lambda h: RC.cpu(h, "darwin", CFG)  # noqa: E731
        self.assertEqual(RC.seen_elsewhere_on_baseline(b, [f], k, {(k[0], k[2]): [dict(b, host=631)]}, cpu_mac), [631])
        self.assertEqual(RC.seen_elsewhere_on_baseline(b, [f], k, {(k[0], k[2]): [dict(b, host=631, first_uninstall_error=None)]}, cpu_mac), [])


class HostOnly(unittest.TestCase):
    K = ("adobe-creative-cloud/windows", 862, "lifecycle")

    def test_passes_on_other_hosts_same_cpu_same_catalog(self):
        b = rec(862, "findings", "install")
        cur = {(self.K[0], self.K[2]): [b, rec(861, "pass"), rec(859, "pass"), rec(863, "pass")]}
        self.assertEqual(RC.passes_elsewhere_on_current(self.K, b, cur, cpu_win), [859, 861])

    def test_other_catalog_does_not_count(self):
        b = rec(862, "findings", "install")
        cur = {(self.K[0], self.K[2]): [b, rec(861, "pass", cid="c0")]}
        self.assertEqual(RC.passes_elsewhere_on_current(self.K, b, cur, cpu_win), [])

    def test_macos_hosts_are_one_group(self):
        k = ("wezterm/darwin", 865, "lifecycle")
        b = rec(865, "findings", "S3")
        cur = {(k[0], k[2]): [b, rec(860, "pass")]}
        self.assertEqual(RC.passes_elsewhere_on_current(k, b, cur, lambda h: RC.cpu(h, "darwin", CFG)), [860])


if __name__ == "__main__":
    unittest.main()
