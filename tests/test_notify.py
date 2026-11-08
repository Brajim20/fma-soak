"""notify.py: an announcement longer than GitHub's body limit is split into parts."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import notify  # noqa: E402


class Parts(unittest.TestCase):
    def test_short_text_is_one_part(self):
        self.assertEqual(notify.parts("a\nb"), ["a\nb"])

    def test_split_keeps_lines_whole_and_under_limit(self):
        lines = [f"- finding {i} " + "x" * 50 for i in range(3000)]
        got = notify.parts("\n".join(lines), limit=1000)
        self.assertTrue(all(len(p) <= 1000 for p in got))
        self.assertEqual("\n".join(got).split("\n"), lines)

    def test_overlong_line_is_cut(self):
        got = notify.parts("y" * 2500, limit=1000)
        self.assertEqual([len(p) for p in got], [1000, 1000, 500])


if __name__ == "__main__":
    unittest.main()
