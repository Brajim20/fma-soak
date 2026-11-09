"""Tests for runner.low_disk: the pre-install free-disk gate. Run: python3 -m unittest discover tests"""
import os, sys, unittest
from unittest import mock

os.environ.setdefault("FMA_SOAK_TOKEN", "test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import runner  # noqa: E402


def host(free, code=200):
    return lambda m, p, *a, **k: (code, {"host": {"gigs_disk_space_available": free}})


class LowDisk(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(runner.CFG, {"disk": {"min_gb": 5}})
        p.start()
        self.addCleanup(p.stop)

    def test_below_floor_skips(self):
        with mock.patch.object(runner, "api", host(0.8)):
            self.assertIn("0.8 GB free", runner.low_disk(631))

    def test_at_or_above_floor_runs(self):
        with mock.patch.object(runner, "api", host(5)):
            self.assertIsNone(runner.low_disk(631))

    def test_unknown_runs(self):
        with mock.patch.object(runner, "api", host(None, code=502)):
            self.assertIsNone(runner.low_disk(631))


if __name__ == "__main__":
    unittest.main()
