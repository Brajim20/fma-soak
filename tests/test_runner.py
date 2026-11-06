"""Pure-function tests for soak/runner.py. Run: python3 -m unittest discover tests"""
import os, sys, unittest

os.environ.setdefault("FMA_SOAK_TOKEN", "test")  # runner reads a token at import; nothing here calls the API
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402


class Vcmp(unittest.TestCase):
    def test_trailing_zeros(self):
        self.assertEqual(runner.vcmp("2.42304.2.0", "2.42304.2"), 0)

    def test_numeric_not_lexical(self):
        self.assertEqual(runner.vcmp("1.10", "1.9"), 1)

    def test_prerelease_below_release(self):
        self.assertEqual(runner.vcmp("160.0b2", "160.0"), -1)
        self.assertEqual(runner.vcmp("2.0-rc.1", "2.0"), -1)
        self.assertEqual(runner.vcmp("2.0-alpha", "2.0-beta"), -1)

    def test_prerelease_order(self):
        self.assertEqual(runner.vcmp("160.0b3", "160.0b2"), 1)
        self.assertEqual(runner.vcmp("160.0b2", "159.0"), 1)

    def test_hash_suffix_is_not_a_prerelease(self):
        self.assertEqual(runner.vkey("1.3.6.1921.g7086c071a617")[1], (4, 0))

    def test_words_are_not_prereleases(self):
        self.assertEqual(runner.vcmp("1.0 (build 5)", "1.0 (build 5)"), 0)


class Scrub(unittest.TestCase):
    def setUp(self):
        runner.SCRUB_NAMES.clear()

    def test_mac_and_windows_paths(self):
        self.assertEqual(runner.scrub("/Users/jane/Library/x"), "/Users/<user>/Library/x")
        self.assertEqual(runner.scrub(r"C:\Users\Jane Doe\AppData\Local\Figma"), r"C:\Users\<user>\AppData\Local\Figma")
        self.assertEqual(runner.scrub("/Users/Shared/x"), "/Users/Shared/x")

    def test_scrub_before_slicing(self):
        out = "x" * 50 + "/Users/jsmith1/Library/Cookies/org.app"
        tail = runner.scrub(out)[-40:]
        self.assertNotIn("jsmith1", tail)

    def test_prose(self):
        for line in ("Restarting Chrome for user: jsmith1", "for user 'jsmith1'", "Console user: jsmith1 (uid 501)",
                     "Assigned ownership of Claude.app to 'jsmith1' so Claude can auto-update."):
            self.assertNotIn("jsmith1", runner.scrub(line), line)

    def test_learned_name_replaced_elsewhere(self):
        runner.scrub("/Users/jsmith1/Library")
        self.assertEqual(runner.scrub("owner jsmith1 ok"), "owner <user> ok")
        self.assertEqual(runner.scrub("com.jsmith1x.app"), "com.jsmith1x.app")

    def test_scrub_all_nested(self):
        rec = {"context": {"install_locations": [r"C:\Users\brayan-x\AppData\Local\Figma"]}, "n": 3, "ok": None}
        self.assertEqual(runner.scrub_all(rec),
                         {"context": {"install_locations": [r"C:\Users\<user>\AppData\Local\Figma"]}, "n": 3, "ok": None})


class Helpers(unittest.TestCase):
    def test_sql_str(self):
        self.assertEqual(runner.sql_str("/Applications/Jane's App.app"), "'/Applications/Jane''s App.app'")

    def test_norm_ignores_numbers(self):
        a = {"shape": "install", "msg": "timed out after 1800 s (pid 4411)"}
        b = {"shape": "install", "msg": "timed out after 1801 s (pid 9)"}
        self.assertEqual(runner.norm(a), runner.norm(b))
        self.assertNotEqual(runner.norm(a), runner.norm({"shape": "S3", "msg": a["msg"]}))


if __name__ == "__main__":
    unittest.main()
