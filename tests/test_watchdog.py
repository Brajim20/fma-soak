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


def sw(status, sw_minutes_ago, platform="darwin"):
    t = (NOW - datetime.timedelta(minutes=sw_minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"status": status, "seen_time": NOW.isoformat().replace("+00:00", "Z"), "software_updated_at": t,
            "platform": platform}


def act(minutes_ago, typ="installed_software", status="installed"):
    return {"type": typ, "created_at": (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
            "details": {"status": status}}


BUSY = lambda hid: [act(10)]   # installed something 10 min ago
IDLE = lambda hid: []


class StaleInventory(unittest.TestCase):
    """An online host whose software inventory stopped (858, 2026-11-19): restart fleetd, then the VM, then alert."""

    def test_only_the_stuck_host_when_others_are_fresh(self):
        hosts = {858: sw("online", 720), 860: sw("online", 30), 865: sw("online", 40)}
        self.assertEqual(list(W.stale_inventory(hosts, NOW, BUSY)), [858])

    def test_everyone_stale_means_fleet_not_the_hosts(self):
        hosts = {858: sw("online", 720), 860: sw("online", 300)}
        self.assertEqual(W.stale_inventory(hosts, NOW, BUSY), {})

    def test_offline_hosts_are_left_to_the_outage_path(self):
        hosts = {858: sw("offline", 720), 860: sw("online", 30)}
        self.assertEqual(W.stale_inventory(hosts, NOW, BUSY), {})

    def test_idle_host_with_an_old_timestamp_is_healthy(self):
        hosts = {860: sw("online", 140), 865: sw("online", 20)}
        self.assertEqual(W.stale_inventory(hosts, NOW, IDLE), {})

    def test_failed_installs_dont_count_as_changes(self):
        hosts = {858: sw("online", 720), 865: sw("online", 20)}
        self.assertEqual(W.stale_inventory(hosts, NOW, lambda h: [act(10, status="failed_install")]), {})

    def test_uninstall_that_leaves_the_app_listed_doesnt_count(self):
        # 860, 2026-11-26: Herdr's staged copy kept it listed through every "successful" uninstall
        hosts = {860: sw("online", 480), 865: sw("online", 20)}
        herdr = lambda hid: [act(10, "uninstalled_software", "uninstalled") | {"details": {"status": "uninstalled", "software_title": "Herdr"}}]
        self.assertEqual(W.stale_inventory(hosts, NOW, herdr, lambda hid: (lambda t: t == "Herdr")), {})

    def test_uninstall_that_removed_the_app_still_counts(self):
        hosts = {860: sw("online", 480), 865: sw("online", 20)}
        gone = lambda hid: [{"type": "uninstalled_software", "created_at": act(10)["created_at"],
                             "details": {"status": "uninstalled", "software_title": "Yubico Authenticator"}}]
        self.assertEqual(list(W.stale_inventory(hosts, NOW, gone, lambda hid: (lambda t: False))), [860])

    def steps(self, state, minutes_later=0, local=None, mine=(858,)):
        hosts = {858: sw("online", 720 + minutes_later), 860: sw("online", 30)}
        now = NOW + datetime.timedelta(minutes=minutes_later)
        with mock.patch.object(W, "run_script", return_value=True) as rs, mock.patch.object(W, "restart") as rv, \
                mock.patch.object(W, "DRY", False), mock.patch.object(W, "activities", lambda hid, tok: [act(5)]):
            msgs, book = W.fix_stale(hosts, state, now, "t", local or {}, set(mine))
        return msgs, book, rs, rv

    def test_first_step_restarts_fleetd(self):
        msgs, book, rs, rv = self.steps({})
        rs.assert_called_once()
        rv.assert_not_called()
        self.assertIn("fleetd", book["858"])

    def test_vm_restart_after_the_wait_when_still_stuck(self):
        _, book, _, _ = self.steps({})
        msgs, book, rs, rv = self.steps({"stale": book}, minutes_later=W.FLEETD_WAIT_MIN + 1, local={858: "vm858"})
        rv.assert_called_once_with("vm858")
        rs.assert_not_called()

    def test_not_a_vm_here_so_alert_once(self):
        _, book, _, _ = self.steps({})
        msgs, book, _, rv = self.steps({"stale": book}, minutes_later=W.FLEETD_WAIT_MIN + 1)
        rv.assert_not_called()
        self.assertTrue(any("didn't fix it" in m for m in msgs))
        msgs, _, _, _ = self.steps({"stale": book}, minutes_later=W.FLEETD_WAIT_MIN + 10)
        self.assertEqual(msgs, [])

    def test_hosts_another_mac_owns_are_left_alone(self):
        msgs, book, rs, _ = self.steps({}, mine=())
        rs.assert_not_called()
        self.assertEqual(book, {})

    def test_recovered_host_is_reported_and_forgotten(self):
        hosts = {858: sw("online", 5), 860: sw("online", 30)}
        msgs, book = W.fix_stale(hosts, {"stale": {"858": {"since": "x", "fleetd": NOW.isoformat()}}}, NOW, "t", {}, {858})
        self.assertEqual(book, {})
        self.assertTrue(any("updating again" in m for m in msgs))
