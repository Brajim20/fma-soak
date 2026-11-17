"""S6 open-app judgement (soak/running.py) against the quit step's output, before and after #59031's force-quit.
Run: python3 -m unittest discover tests"""
import os, sys, unittest

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import running  # noqa: E402

B = "com.tinyspeck.slackmacgap"
RUNNING = f"Application '{B}' was running; will relaunch after installation."
QUITTING = f"Quitting application '{B}'..."
NOQUIT = f"Application '{B}' did not quit."
LASTERR = "Last quit error: 31:42: execution error: AppleEvent timed out. (-1712)"
T = {"plat": "darwin", "app": "/Applications/Slack.app", "exe": "/Applications/Slack.app/Contents/MacOS/Slack"}

OLD_REFUSED = [RUNNING, QUITTING, NOQUIT]  # the dogfood Slack log, script from before #59031
FORCED = [RUNNING, QUITTING, NOQUIT, LASTERR, f"Stopping application '{B}'...", f"Application '{B}' stopped.",
          f"Application '{B}' relaunched successfully."]
KILLED = [RUNNING, QUITTING, NOQUIT, f"Stopping application '{B}'...",
          f"Application '{B}' didn't stop; forcing it to quit.", f"Application '{B}' stopped."]
FORCE_FAILED = [RUNNING, QUITTING, NOQUIT, LASTERR, f"Stopping application '{B}'...",
                f"Application '{B}' didn't stop; forcing it to quit.", f"Application '{B}' did not stop."]
NO_PROCESS = [RUNNING, QUITTING, NOQUIT, f"Found no process to stop for application '{B}'."]


def judge(lines, status, before=("100",), after=None):
    rec, found = {}, []
    after = [{"pid": "200", "path": T["exe"], "on_disk": "1"}] if after is None else after
    running.judge_open(rec, lambda kind, msg: found.append(msg), T, "k", "reinstall over the open app",
                       set(before), after, status, lines)
    return rec, found


class QuitOutcome(unittest.TestCase):
    def test_polite_quit(self):
        self.assertIsNone(running.quit_outcome([RUNNING, QUITTING, f"Application '{B}' quit successfully."]))

    def test_pre_59031_refusal(self):
        self.assertEqual(running.quit_outcome(OLD_REFUSED), "refused")

    def test_sigterm_and_sigkill_both_count_as_forced(self):
        self.assertEqual(running.quit_outcome(FORCED), "forced")
        self.assertEqual(running.quit_outcome(KILLED), "forced")

    def test_force_failed(self):
        self.assertEqual(running.quit_outcome(FORCE_FAILED), "force_failed")
        self.assertEqual(running.quit_outcome(NO_PROCESS), "force_failed")

    def test_reason(self):
        self.assertIn("(-1712)", running.quit_reason(FORCED))
        self.assertIsNone(running.quit_reason(OLD_REFUSED))


class JudgeOpen(unittest.TestCase):
    def test_old_script_refusal_is_a_note(self):
        rec, found = judge(OLD_REFUSED, "failed_install", after=[{"pid": "100", "path": T["exe"], "on_disk": "1"}])
        self.assertEqual(found, [])
        self.assertIn("no #59031 force-quit", rec["k_running_note"])
        self.assertEqual(rec["k_quit"], "refused")

    def test_forced_install_that_relaunched_is_a_note(self):
        rec, found = judge(FORCED, "installed")
        self.assertEqual(found, [])
        self.assertEqual(rec["k_quit"], "forced")
        self.assertIn("(-1712)", rec["k_running_note"])

    def test_force_quit_failure_is_a_finding(self):
        rec, found = judge(FORCE_FAILED, "failed_install", after=[{"pid": "100", "path": T["exe"], "on_disk": "1"}])
        self.assertEqual(len(found), 1)
        self.assertIn("did not stop", found[0])
        rec, found = judge(NO_PROCESS, "failed_install", after=[{"pid": "100", "path": T["exe"], "on_disk": "1"}])
        self.assertIn("Found no process", found[0])

    def test_forced_but_not_reopened_is_still_56809(self):
        rec, found = judge(FORCED, "installed", after=[])
        self.assertEqual(len(found), 1)
        self.assertIn("#56809", found[0])


if __name__ == "__main__":
    unittest.main()
