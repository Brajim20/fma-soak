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


if __name__ == "__main__":
    unittest.main()
