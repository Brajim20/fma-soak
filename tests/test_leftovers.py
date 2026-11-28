"""leftovers.py diff, with the snapshots faked. Run: python3 -m unittest discover -s tests"""
import os, sys, unittest

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import leftovers as L  # noqa: E402


def snaps(*sets):
    """A snap() that answers each phase in turn (None = the host didn't answer)."""
    it = iter(sets)
    return lambda host, plat: (lambda s: None if s is None else set(s))(next(it))


def run(plat, s0, s1, s2, app_gone=True, slug="app/darwin", host=1):
    rec, found = {"slug": slug}, []
    snap = snaps(s0, s1, s2)
    L.start(host, plat, rec, snap)
    L.after_install(host, plat, rec, snap)
    L.finish(host, plat, rec, lambda shape, msg: found.append((shape, msg)), app_gone, snap)
    return rec, found


BASE = ["/Applications/Safari.app", "/Library/Preferences/com.apple.x.plist", "receipt:com.apple.pkg.X"]


class Diff(unittest.TestCase):
    def setUp(self):
        L.SNAPS.clear(); L.CARRY.clear(); L.RAW.clear()

    def test_clean_uninstall_has_no_finding(self):
        rec, found = run("darwin", BASE, BASE + ["/Applications/Foo.app"], BASE)
        self.assertEqual(found, [])
        self.assertEqual(rec["leftovers"]["created"], 1)
        self.assertEqual(rec["leftovers"]["left"], 0)

    def test_daemon_left_behind_is_a_finding(self):
        d = "/Library/LaunchDaemons/com.foo.helper.plist"
        rec, found = run("darwin", BASE, BASE + ["/Applications/Foo.app", d], BASE + [d])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "leftover")
        self.assertIn(d, found[0][1])
        self.assertEqual(rec["leftovers"]["items"], {"launchd": [d]})

    def test_user_data_is_recorded_not_a_finding(self):
        p = "/Library/Application Support/Foo"
        rec, found = run("darwin", BASE, BASE + ["/Applications/Foo.app", p], BASE + [p])
        self.assertEqual(found, [])
        self.assertEqual(rec["leftovers"]["items"], {"data": [p]})

    def test_host_churn_is_ignored(self):
        # removed by something else mid-cycle, or added after the install: neither was created by the install
        rec, found = run("darwin", BASE + ["/Applications/Old.app"], BASE + ["/Applications/Foo.app"],
                         BASE + ["/Applications/Decoy.app"])
        self.assertEqual(found, [])
        self.assertEqual(rec["leftovers"]["left"], 0)

    def test_app_still_there_is_not_double_reported(self):
        a = "/Applications/Foo.app"
        rec, found = run("darwin", BASE, BASE + [a], BASE + [a], app_gone=False)
        self.assertEqual(found, [])  # the S3 / uninstall finding covers it
        self.assertEqual(rec["leftovers"]["items"], {"app": [a]})

    def test_no_answer_turns_the_check_off(self):
        rec, found = run("darwin", BASE, None, BASE)
        self.assertEqual(found, [])
        self.assertFalse(rec["leftovers"]["ok"])
        self.assertIn("snapshot 1", rec["leftovers"]["error"])
        self.assertEqual(L.SNAPS, {})

    def test_install_that_failed_skips_the_check(self):
        rec, found = {"slug": "app/darwin"}, []
        snap = snaps(BASE, BASE)
        L.start(1, "darwin", rec, snap)  # no after_install: the first install didn't succeed
        L.finish(1, "darwin", rec, lambda *a: found.append(a), True, snap)
        self.assertEqual(found, [])
        self.assertNotIn("left", rec["leftovers"])
        self.assertEqual(L.SNAPS, {})

    def test_coexist_mode_is_skipped(self):
        rec = {"slug": "onedrive/windows", "coexist_per_user": True}
        L.start(1, "windows", rec, snaps(BASE))
        self.assertNotIn("leftovers", rec)

    def test_retry_reports_the_first_runs_leftovers(self):
        # first run leaves a service; the retry's snapshot 0 already has it, so it isn't "created" again
        svc = "service:FooUpdate"
        prog = "dir:C:\\Program Files\\Foo"
        _, f1 = run("windows", ["service:Spooler"], ["service:Spooler", svc, prog], ["service:Spooler", svc],
                    slug="foo/windows")
        self.assertEqual(len(f1), 1)
        L.carry({"slug": "foo/windows"}, 1)
        _, f2 = run("windows", ["service:Spooler", svc], ["service:Spooler", svc, prog], ["service:Spooler", svc],
                    slug="foo/windows")
        self.assertEqual(len(f2), 1)
        self.assertIn(svc, f2[0][1])

    def test_os_churn_is_ignored(self):
        churn = ["task:\\Microsoft\\Windows\\EnterpriseMgmt\\SessionRetry\\X", "service:CDPUserSvc_6f65b"]
        task = "task:\\FooUpdateTask"
        rec, found = run("windows", ["service:Spooler"], ["service:Spooler", task] + churn,
                         ["service:Spooler", task] + churn, slug="foo/windows")
        self.assertEqual(rec["leftovers"]["items"], {"task": [task]})
        self.assertEqual(len(found), 1)

    def test_carry_only_counts_items_still_there(self):
        L.CARRY["foo/windows@1"] = ["service:Gone"]
        _, found = run("windows", ["service:Spooler"], ["service:Spooler"], ["service:Spooler"], slug="foo/windows")
        self.assertEqual(found, [])


class Kind(unittest.TestCase):
    def test_mac(self):
        k = lambda i: L.kind("darwin", i)
        self.assertEqual(k("/Applications/Foo.app"), "app")
        self.assertEqual(k("/Users/jane/Applications/Foo.app"), "app")
        self.assertEqual(k("/Users/jane/Library/LaunchAgents/com.foo.plist"), "launchd")
        self.assertEqual(k("/Library/PrivilegedHelperTools/com.foo.helper"), "helper")
        self.assertEqual(k("sysext:com.foo.network"), "extension")
        self.assertEqual(k("receipt:com.foo.pkg"), "receipt")
        self.assertEqual(k("/Users/jane/Library/Caches/com.foo"), "data")

    def test_windows(self):
        k = lambda i: L.kind("windows", i)
        self.assertEqual(k("dir:C:\\Program Files (x86)\\Foo"), "program_dir")
        self.assertEqual(k("dir:C:\\Users\\jane\\AppData\\Local\\Programs\\Foo"), "program_dir")
        self.assertEqual(k("dir:C:\\Users\\jane\\AppData\\Roaming\\Foo"), "data")
        self.assertEqual(k("dir:C:\\ProgramData\\Foo"), "data")
        self.assertEqual(k("key:HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Foo"),
                         "uninstall_key")
        self.assertEqual(k("task:\\FooUpdateTask"), "task")
        self.assertEqual(k("run:HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run\\Foo"), "run_key")


class Query(unittest.TestCase):
    def test_no_script_text_the_firewall_blocks(self):
        # qa.fleetdm.com's WAF 403s on ${env:...(x86)}; the queries never carry PowerShell or shell syntax
        for q in L.QUERY.values():
            self.assertNotIn("${", q)
            self.assertNotIn("$env", q)


if __name__ == "__main__":
    unittest.main()
