"""teeth.py seeded failures, with the host faked. Run: python3 -m unittest discover -s tests"""
import os, sys, unittest

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import teeth as T  # noqa: E402


class FakeHost:
    """A host where the fake app is planted or not, and the queries answer honestly unless told otherwise."""
    def __init__(self, plat, answer=None, plant_fails=False):
        self.plat, self.planted, self.answer, self.plant_fails = plat, False, answer, plant_fails
        self.scripts = []

    def run_script(self, host, body, timeout=0):
        self.scripts.append(body)
        if body == T.PLANT[self.plat]:
            if self.plant_fails:
                return {"exit_code": 1, "output": "denied"}
            self.planted = True
            return {"exit_code": 0, "output": "planted"}
        self.planted = False
        return {"exit_code": 0, "output": "removed"}

    def present(self, host, query):
        if self.answer is not None:
            return self.answer
        if "NOT EXISTS" in query:  # patched: no copy older than the version in the query
            return not (self.planted and ("'2.0.0'" in query))
        return self.planted


class Check(unittest.TestCase):
    def test_honest_host_passes_all_five(self):
        for plat in ("darwin", "windows"):
            h = FakeHost(plat)
            rows = T.check(plat, 1, h.present, h.run_script)
            self.assertEqual([r["name"] for r in rows], ["absent", "leftover", "outdated", "current", "removed"])
            self.assertEqual(T.verdict(rows), "ok")
            self.assertFalse(h.planted)

    def test_query_that_always_says_no_rows_loses_teeth(self):
        h = FakeHost("darwin", answer=False)
        v = T.verdict(T.check("darwin", 1, h.present, h.run_script))
        self.assertTrue(v.startswith("lost teeth"))
        self.assertIn("leftover answered False, expected True", v)
        self.assertIn("current answered False", v)

    def test_query_that_always_matches_loses_teeth(self):
        h = FakeHost("windows", answer=True)
        v = T.verdict(T.check("windows", 1, h.present, h.run_script))
        self.assertIn("absent answered True", v)
        self.assertIn("outdated answered True", v)

    def test_no_answer_is_inconclusive_not_lost(self):
        h = FakeHost("darwin", answer=None)
        h.present = lambda host, q: None
        self.assertTrue(T.verdict(T.check("darwin", 1, h.present, h.run_script)).startswith("inconclusive"))

    def test_failed_plant_is_inconclusive_and_cleans_up(self):
        h = FakeHost("darwin", plant_fails=True)
        rows = T.check("darwin", 1, h.present, h.run_script)
        self.assertTrue(T.verdict(rows).startswith("inconclusive"))
        self.assertEqual(h.scripts[-1], T.REMOVE["darwin"])

    def test_queries_follow_the_catalog_shape(self):
        exists, patched = T.queries("darwin")
        self.assertEqual(exists, "SELECT 1 FROM apps WHERE bundle_identifier = 'com.fleetdm.fma-soak.teeth';")
        self.assertIn("version_compare(bundle_short_version, '2.0.0') < 0", patched("2.0.0"))
        exists, patched = T.queries("windows")
        self.assertIn("FROM programs WHERE name = 'FMA Soak Teeth' AND publisher = 'fma-soak'", exists)


if __name__ == "__main__":
    unittest.main()
