import datetime, importlib.util, os, unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "vm_watchdog", os.path.join(os.path.dirname(__file__), "..", "watchdog", "vm-watchdog.py"))
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

NOW = datetime.datetime(2026, 11, 11, 12, 0, tzinfo=datetime.timezone.utc)


def h(status, minutes_ago, name="vm"):
    t = (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"status": status, "seen_time": t, "hostname": name}


class Outages(unittest.TestCase):
    """One Slack message per batch of hosts past ALERT_MIN offline, once per outage, and one when they're back."""

    def test_a_whole_mac_going_down_is_one_message(self):
        hosts = {629: h("offline", 300), 631: h("offline", 300), 636: h("offline", 300), 624: h("online", 1)}
        msgs, alerted = W.outages(hosts, {}, NOW)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(all(str(x) in msgs[0] for x in (629, 631, 636)) and "624" not in msgs[0])
        self.assertEqual(set(alerted), {"629", "631", "636"})

    def test_under_the_threshold_says_nothing(self):
        self.assertEqual(W.outages({631: h("offline", W.ALERT_MIN - 5)}, {}, NOW), ([], {}))

    def test_alerts_once_per_outage(self):
        _, alerted = W.outages({631: h("offline", 300)}, {}, NOW)
        self.assertEqual(W.outages({631: h("offline", 400)}, {"alerted": alerted}, NOW), ([], alerted))

    def test_back_online_clears_and_says_so(self):
        msgs, alerted = W.outages({631: h("online", 1)}, {"alerted": {"631": "2026-11-11T07:00Z"}}, NOW)
        self.assertEqual(alerted, {})
        self.assertEqual(len(msgs), 1)
        self.assertIn("back online: 631", msgs[0])


class Restart(unittest.TestCase):
    def test_parallels_names_use_prlctl(self):
        with mock.patch.object(W.subprocess, "run") as run, mock.patch.object(W.time, "sleep"):
            W.restart("parallels:Windows 11")
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [[W.PRLCTL, "stop", "Windows 11", "--kill"], [W.PRLCTL, "start", "Windows 11"]])

    def test_plain_names_stay_utm(self):
        with mock.patch.object(W, "utm") as utm, mock.patch.object(W.time, "sleep"):
            W.restart("macOS new vm for upadte")
        self.assertEqual([c.args for c in utm.call_args_list],
                         [("stop", "macOS new vm for upadte"), ("start", "macOS new vm for upadte")])


if __name__ == "__main__":
    unittest.main()
