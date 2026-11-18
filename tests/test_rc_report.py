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


if __name__ == "__main__":
    unittest.main()
