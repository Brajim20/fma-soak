"""Tests for runner.answers_live_query: pool runners wait until live queries work. Run: python3 -m unittest discover tests"""
import os, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402


class AnswersLiveQuery(unittest.TestCase):
    def test_waits_until_the_host_answers(self):
        replies = iter([(400, {"message": "no hosts targeted"}), (200, {"error": "timeout"}), (200, {"rows": [{"1": "1"}]})])
        with mock.patch.object(runner, "api", lambda *a, **k: next(replies)), mock.patch.object(runner.time, "sleep"):
            self.assertTrue(runner.answers_live_query(744))

    def test_gives_up_after_the_wait(self):
        clock = iter(range(0, 10_000, 100))
        with mock.patch.object(runner, "api", lambda *a, **k: (400, {"message": "no hosts targeted"})), \
                mock.patch.object(runner.time, "sleep"), mock.patch.object(runner.time, "time", lambda: next(clock)):
            self.assertFalse(runner.answers_live_query(744, minutes=5))


if __name__ == "__main__":
    unittest.main()
