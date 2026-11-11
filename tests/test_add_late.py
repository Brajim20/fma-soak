"""Tests for runner.ensure_title when the add request fails but Fleet adds the app anyway. Run: python3 -m unittest discover tests"""
import os, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402

SLUG = "qgis@ltr/darwin"


def catalog(tid):
    return 200, {"fleet_maintained_apps": [{"id": 7, "slug": SLUG, "software_title_id": tid}]}


class EnsureTitleAfterProxyTimeout(unittest.TestCase):
    def run_add(self, replies):
        state = {"harness_titles": {}}
        with mock.patch.object(runner, "api", lambda *a, **k: next(replies)), mock.patch.object(runner.time, "sleep"), \
                mock.patch.object(runner, "save_state"):
            return runner.ensure_title(SLUG, state, 773), state

    def test_title_added_after_a_524_counts_as_the_harness_add(self):
        replies = iter([catalog(None), (524, {"message": "error code: 524"}), catalog(None), catalog(33977)])
        (tid, added), state = self.run_add(replies)
        self.assertEqual((tid, added), (33977, True))
        self.assertEqual(state["harness_titles"][SLUG], 33977)

    def test_still_fails_when_the_title_never_shows_up(self):
        clock = iter(range(0, 10_000, 100))
        replies = iter([catalog(None), (502, {"message": "error code: 502"})] + [catalog(None)] * 50)
        with mock.patch.object(runner.time, "time", lambda: next(clock)):
            with self.assertRaisesRegex(RuntimeError, "add failed 502"):
                self.run_add(replies)

    def test_a_422_is_not_waited_on(self):
        replies = iter([catalog(None), (422, {"message": "Validation Failed"})])
        with self.assertRaisesRegex(RuntimeError, "add failed 422"):
            self.run_add(replies)


if __name__ == "__main__":
    unittest.main()
