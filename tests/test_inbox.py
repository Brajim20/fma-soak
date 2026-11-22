"""INBOX.md coverage in soak/triage_queue.py. Run: python3 -m unittest discover tests"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import digest as D  # noqa: E402
import triage_queue as T  # noqa: E402


def run(root, name, decisions):
    os.makedirs(os.path.join(root, name))
    json.dump(decisions, open(os.path.join(root, name, "decisions.json"), "w"))


BUG = lambda slug: {"slug": slug, "decision": "bug", "summary": slug, "titles": [slug], "drafts": ["d.md"], "hosts": []}


class Inbox(unittest.TestCase):
    def setUp(self):
        self.saved = list(D.TRIAGE)
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        D.TRIAGE[:] = self.saved

    def text(self):
        return open(os.path.join(self.root, "INBOX.md")).read()

    def test_older_entry_does_not_hide_a_later_draft(self):
        # the 2026-11-22 audit case: an 11-07 outage verdict hid an 11-21 draft
        D.TRIAGE[:] = [{"app": "maestral/darwin", "verdict": "environment: outage", "added": "2026-11-07"}]
        run(self.root, "2026-11-21-0700", [BUG("maestral/darwin")])
        self.assertEqual(T.inbox(self.root), (1, 0))
        self.assertIn("maestral/darwin", self.text())

    def test_entry_added_the_same_day_or_later_covers_it(self):
        D.TRIAGE[:] = [{"app": "a/darwin@858", "verdict": "filed #59999: x", "added": "2026-11-21"},
                       {"app": "b/windows", "verdict": "rejected: y", "added": "2026-11-22"}]
        run(self.root, "2026-11-21-0700", [BUG("a/darwin"), BUG("b/windows")])
        self.assertEqual(T.inbox(self.root), (0, 0))

    def test_newest_decision_wins(self):
        D.TRIAGE[:] = []
        run(self.root, "2026-11-20-0700", [BUG("c/darwin")])
        run(self.root, "2026-11-21-0700", [{"slug": "c/darwin", "decision": "environment", "summary": ""}])
        self.assertEqual(T.inbox(self.root), (0, 0))


if __name__ == "__main__":
    unittest.main()
