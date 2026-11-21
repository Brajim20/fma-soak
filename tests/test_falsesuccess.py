import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "soak"))
import falsesuccess as FS


class Verdict(unittest.TestCase):
    def test_nonzero_exit_fails_as_it_should(self):
        self.assertEqual(FS.verdict({"exit_code": 0, "output": "ditto: error\nFS_EXIT=1"})[0], "fails")

    def test_zero_exit_is_false_success(self):
        self.assertEqual(FS.verdict({"exit_code": 0, "output": "copied\nFS_EXIT=0"})[0], "FALSE SUCCESS")

    def test_hang_wins_over_exit(self):
        self.assertEqual(FS.verdict({"exit_code": 0, "output": "FS_HANG\nFS_EXIT=143"})[0], "hangs")

    def test_no_result_is_error(self):
        self.assertEqual(FS.verdict({"error": "no result in time"})[0], "error")
        self.assertEqual(FS.verdict({"exit_code": 1, "output": "partial"})[0], "error")


class Body(unittest.TestCase):
    def test_placeholders_filled(self):
        for plat, kind in (("darwin", "zip"), ("darwin", "dmg"), ("windows", "msi"), ("windows", "zip")):
            b = FS.probe_body(plat, kind, "App 1.0 (x64).zip", "echo hi")
            self.assertNotIn("__", b.replace("__file__", ""))
            self.assertIn("FS_EXIT=", b)


class Pick(unittest.TestCase):
    def test_new_scripts_first_and_every_kind(self):
        cat = {"a/darwin": ("zip", "a.zip", "s", "1", None), "b/darwin": ("dmg", "b.dmg", "s", "2", None),
               "c/darwin": ("zip", "c.zip", "s", "3", None), "d/darwin": ("pkg", "d.pkg", "s", "4", None)}
        saved = FS.catalog, FS.target
        FS.catalog, FS.target = (lambda plat: sorted(cat)), (lambda s: cat[s])
        try:
            ledger = {"a/darwin": {"date": "2026-11-01", "script": "1"}, "b/darwin": {"date": "2026-11-02", "script": "old"}}
            got = [r[3] for r in FS.pick("darwin", ledger, 3)]
        finally:
            FS.catalog, FS.target = saved
        self.assertEqual(len(got), 3)
        self.assertNotIn("a/darwin", got)  # checked, unchanged: last
        self.assertEqual({FS.target(s)[0] if False else cat[s][0] for s in got}, {"zip", "dmg", "pkg"})


if __name__ == "__main__":
    unittest.main()


class Kind(unittest.TestCase):
    def test_extension_then_script(self):
        self.assertEqual(FS.kind_of("App.dmg", ""), "dmg")
        self.assertEqual(FS.kind_of("stable", 'ditto -x -k "$INSTALLER_PATH" "$TMPDIR"'), "zip")
        self.assertEqual(FS.kind_of("download", "Start-Process msiexec -ArgumentList"), "msi")
        self.assertEqual(FS.kind_of("download", "Start-Process $env:INSTALLER_PATH"), "exe")


class AppName(unittest.TestCase):
    def test_from_script(self):
        self.assertEqual(FS.app_name('cp -R "$TMPDIR/Visual Studio Code.app" "$APPDIR"'), "Visual Studio Code.app")
        self.assertEqual(FS.app_name("echo nothing"), "QA Probe.app")
