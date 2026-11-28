"""Phase 3 standard-user helpers. Run: python3 -m unittest discover -s tests"""
import os, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner as R  # noqa: E402
import standard_user as S  # noqa: E402


class ConsoleAdmin(unittest.TestCase):
    def ask(self, st, d, plat="darwin"):
        with mock.patch.object(R, "api", return_value=(st, d)):
            return R.console_admin(1, plat)

    def test_answers(self):
        self.assertIs(self.ask(200, {"rows": [{"admin": "1"}]}), True)
        self.assertIs(self.ask(200, {"rows": [{"admin": "0"}]}, "windows"), False)

    def test_nobody_signed_in_or_no_answer_is_unknown(self):
        self.assertIsNone(self.ask(200, {"rows": []}))
        self.assertIsNone(self.ask(200, {"rows": [{"admin": "1"}], "error": "timeout"}))
        self.assertIsNone(self.ask(500, {}))
        self.assertIsNone(R.console_admin(1, "linux"))


class Scripts(unittest.TestCase):
    def test_action_is_filled_in(self):
        for plat in ("darwin", "windows"):
            for act in S.ACTIONS:
                body = S.script(plat, act)
                self.assertNotIn("__ACTION__", body)
                self.assertIn(act, body)

    def test_inert_wrapper_carries_the_text_and_runs_nothing(self):
        mac = S.inert("darwin", S.script("darwin", "create"))
        self.assertTrue(mac.startswith(": <<'FMA_SOAK_INERT'\n") and mac.endswith("FMA_SOAK_INERT\necho inert-ok"))
        win = S.inert("windows", S.script("windows", "create"))
        self.assertTrue(win.startswith("$null = @'\n") and win.endswith("\n'@\n'inert-ok'"))
        self.assertIn("New-LocalUser", win)

    def test_scripts_never_print_the_password(self):
        mac, win = S.script("darwin", "create"), S.script("windows", "create")
        self.assertNotRegex(mac, r'echo[^\n]*\$\(cat "\$PW"\)')
        self.assertNotRegex(win, r"^\s*\$pw\s*$")


if __name__ == "__main__":
    unittest.main()
