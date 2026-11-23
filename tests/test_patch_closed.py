"""soak/patch_closed.py: how the patch policy is turned on and read back. Run: python3 -m unittest discover tests"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soak"))
import patch_closed as P  # noqa: E402


class PatchOn(unittest.TestCase):
    def setUp(self):
        self.calls, self.saved = [], P.R.api
        P.R.api = lambda method, path, body=None, **kw: self.calls.append((method, path, body)) or (200, {})

    def tearDown(self):
        P.R.api = self.saved

    def test_creates_the_policy_like_the_deploy_modal(self):
        # the package API (patch=true) leaves install_software null (#57141); the Deploy modal's request doesn't
        P.patch_on(18027)
        method, path, body = self.calls[0]
        self.assertEqual((method, path), ("POST", f"/fleets/{P.R.FID}/policies"))
        self.assertEqual(body["type"], "patch")
        self.assertEqual(body["patch_software_title_id"], 18027)
        self.assertEqual(body["software_title_id"], 18027)
        self.assertTrue(body["patch_when_closed"] and body["continuous_automations_enabled"])
        self.assertFalse(body["notify_before_patching"] or body["patch_deadline"])


class Summary(unittest.TestCase):
    def test_policy_without_install_automation_has_no_install_title(self):
        # policy 1259 (2026-11-22, 865): patch_software set, install_software null
        s = P.policy_summary({"id": 1259, "patch_when_closed": True, "continuous_automations_enabled": True,
                              "patch_software": {"software_title_id": 18027}, "install_software": None})
        self.assertEqual(s["patch_title"], 18027)
        self.assertIsNone(s["install_title"])

    def test_working_policy_names_the_title_twice(self):
        s = P.policy_summary({"id": 1, "patch_software": {"software_title_id": 7}, "install_software": {"software_title_id": 7}})
        self.assertEqual((s["patch_title"], s["install_title"]), (7, 7))


if __name__ == "__main__":
    unittest.main()
