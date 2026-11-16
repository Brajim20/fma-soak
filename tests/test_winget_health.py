"""Tests for audit/winget_health.py against saved winget-pkgs layouts: the escapes it exists for (#58816 PyCharm,
#58817 Tableau, #51801 IntelliJ) must be flagged, and look-alikes must not. Run: python3 -m unittest discover tests"""
import datetime, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit"))
import winget_health as W  # noqa: E402

TODAY = datetime.date(2026, 11, 16)


class FakeWinget:
    """tree: {path: [sub-folder names]}; added: {path: date}; text: {path: yaml}."""

    def __init__(self, tree, added=None, text=None):
        self.tree, self._added, self._text = tree, added or {}, text or {}

    def dirs(self, path):
        return self.tree.get(path)

    def files(self, path):
        pkg = ".".join(path.split("/")[2:-1])
        return [f"{pkg}.installer.yaml", f"{pkg}.locale.en-US.yaml"]

    def added(self, path):
        return self._added.get(path)

    def text(self, path):
        return self._text.get(path)


def locale(pub):
    return f"PackageIdentifier: X\nPublisher: {pub}\nPackageName: X\n"


def app(slug, name, version, pkg=None, publisher=None):
    m = {"versions": [{"version": version}]}
    i = {"package_identifier": pkg, **({"program_publisher": publisher} if publisher else {})} if pkg else {}
    return {"slug": slug, "name": name}, m, i


def run(entries, w, changed=None, **kw):
    apps, man, inp = [], {}, {}
    for a, m, i in entries:
        apps.append(a)
        man[a["slug"]] = m
        if i:
            inp[a["slug"]] = i
    return W.check(apps, man, inp, w, lambda s: (changed or {}).get(s), today=TODAY, **kw)


class Successor(unittest.TestCase):
    def test_pycharm_professional_is_superseded_by_jetbrains_pycharm(self):  # #58816
        w = FakeWinget({"manifests/j/JetBrains/PyCharm/Professional": ["2024.3.5", "2024.3.6.1"],
                        "manifests/j/JetBrains/PyCharm": ["2025.3", "2026.3", "Community", "EAP", "Professional"]})
        succ, *_ = run([app("pycharm/windows", "PyCharm", "2024.3.6.1", "JetBrains.PyCharm.Professional")], w)
        self.assertEqual([(x["successor"], x["successor_latest"]) for x in succ], [("JetBrains.PyCharm", "2026.3")])

    def test_intellij_ultimate_shape_is_flagged(self):  # #51801, before the repoint
        w = FakeWinget({"manifests/j/JetBrains/IntelliJIDEA/Ultimate": ["2025.1.1"],
                        "manifests/j/JetBrains/IntelliJIDEA": ["2025.2", "2026.3", "Community", "Ultimate"]})
        succ, *_ = run([app("intellij-idea/windows", "IntelliJ IDEA", "2025.1.1", "JetBrains.IntelliJIDEA.Ultimate")], w)
        self.assertEqual(len(succ), 1)

    def test_a_current_child_package_is_not_flagged(self):
        # Google.Chrome.Dev runs ahead of Google.Chrome: the parent isn't newer
        w = FakeWinget({"manifests/g/Google/Chrome/Dev": ["144.0.7500.2"],
                        "manifests/g/Google/Chrome": ["143.0.7499.40", "Beta", "Dev"]})
        succ, *_ = run([app("chrome-dev/windows", "Chrome Dev", "144.0.7500.2", "Google.Chrome.Dev")], w)
        self.assertEqual(succ, [])

    def test_release_lines_named_in_the_slug_are_left_alone(self):
        tree = {"manifests/m/Microsoft/DotNet/Runtime/10": ["10.0.15"], "manifests/m/Microsoft/DotNet/Runtime": ["8", "9", "10", "11"],
                "manifests/m/Mozilla/Firefox/ESR": ["153.7.0"], "manifests/m/Mozilla/Firefox": ["160.0", "ESR"]}
        succ, *_ = run([app("microsoft-dotnet-runtime-10/windows", ".NET Runtime 10", "10.0.15", "Microsoft.DotNet.Runtime.10"),
                        app("firefox@esr/windows", "Firefox ESR", "153.7.0", "Mozilla.Firefox.ESR")], FakeWinget(tree))
        self.assertEqual(succ, [])

    def test_a_pinned_line_the_slug_doesnt_name_is_flagged_when_a_newer_line_exists(self):
        tree = {"manifests/t/TechSmith/Snagit/2026": ["26.4.0"], "manifests/t/TechSmith/Snagit": ["2025", "2026", "2027"]}
        succ, *_ = run([app("snagit/windows", "Snagit", "26.4.0", "TechSmith.Snagit.2026")], FakeWinget(tree))
        self.assertEqual([(x["successor"], x["kind"]) for x in succ], [("TechSmith.Snagit.2027", "newer release line")])

    def test_a_version_folder_next_to_a_pinned_line_is_not_a_newer_line(self):
        # GIMP.GIMP holds versions (3.2.6) and the child package 3: only whole-number folders are release lines
        tree = {"manifests/g/GIMP/GIMP/3": ["3.2.8.0"], "manifests/g/GIMP/GIMP": ["2.10.38", "3", "3.2.6"]}
        succ, *_ = run([app("gimp/windows", "GIMP", "3.2.8.0", "GIMP.GIMP.3")], FakeWinget(tree))
        self.assertEqual(succ, [])

    def test_reviewed_false_alarms_are_dropped_per_check(self):
        tree = {"manifests/t/TechSmith/Snagit/2026": ["26.4.0"], "manifests/t/TechSmith/Snagit": ["2026", "2027"]}
        e = [app("snagit/windows", "Snagit", "26.4.0", "TechSmith.Snagit.2026")]
        succ, *_ = run(e, FakeWinget(tree), ignore={"successor_package": {"snagit/windows": "why"}})
        self.assertEqual(succ, [])
        succ, *_ = run(e, FakeWinget(tree), ignore={"dormant_source": {"snagit/windows": "why"}})
        self.assertEqual(len(succ), 1)

    def test_a_child_folder_that_isnt_a_version_is_ignored(self):
        tree = {"manifests/a/Adobe/Acrobat/Reader/64-bit": ["26.003.22075"], "manifests/a/Adobe/Acrobat/Reader": ["32-bit", "64-bit"]}
        succ, *_ = run([app("adobe-acrobat-reader/windows", "Acrobat Reader", "26.003.22075", "Adobe.Acrobat.Reader.64-bit")], FakeWinget(tree))
        self.assertEqual(succ, [])

    def test_a_sibling_product_is_not_a_successor(self):
        # Tableau.PrepBuilder next to Tableau.Desktop: two-part ids have no parent package
        w = FakeWinget({"manifests/t/Tableau/Desktop": ["24.3.965"], "manifests/t/Tableau": ["Desktop", "PrepBuilder"]})
        succ, *_ = run([app("tableau-desktop/windows", "Tableau Desktop", "24.3.965", "Tableau.Desktop")], w)
        self.assertEqual(succ, [])


class Dormant(unittest.TestCase):
    TREE = {"manifests/t/Tableau/Desktop": ["24.3.746", "24.3.965"]}
    ADDED = {"manifests/t/Tableau/Desktop/24.3.965": "2025-03-06"}

    def entries(self):
        return [app("tableau-desktop/windows", "Tableau Desktop", "24.3.965", "Tableau.Desktop"),
                app("tableau/darwin", "Tableau Desktop", "2026.3.0")]

    def test_tableau_desktop_is_dormant_while_macos_moved(self):  # #58817
        _, dorm, *_ = run(self.entries(), FakeWinget(self.TREE, self.ADDED), {"tableau/darwin": "2026-11-07"})
        self.assertEqual([(x["slug"], x["macos"], x["signal"]) for x in dorm],
                         [("tableau-desktop/windows", "tableau/darwin", "macOS moved since")])

    def test_paired_by_name_when_slugs_differ(self):
        pair = W.counterparts([a for a, _, _ in self.entries()], {a["slug"]: m for a, m, _ in self.entries()})
        self.assertEqual(pair, {"tableau-desktop/windows": "tableau/darwin"})

    def test_quiet_app_whose_macos_side_is_quiet_too_is_not_flagged(self):
        e = [app("putty/windows", "PuTTY", "0.83", "PuTTY.PuTTY"), app("putty/darwin", "PuTTY", "0.83")]
        w = FakeWinget({"manifests/p/PuTTY/PuTTY": ["0.83"]}, {"manifests/p/PuTTY/PuTTY/0.83": "2025-02-08"})
        _, dorm, *_ = run(e, w, {"putty/darwin": "2025-01-20"})
        self.assertEqual(dorm, [])

    def test_recent_release_is_not_dormant(self):
        w = FakeWinget(self.TREE, {"manifests/t/Tableau/Desktop/24.3.965": "2026-10-01"})
        _, dorm, *_ = run(self.entries(), w, {"tableau/darwin": "2026-11-07"})
        self.assertEqual(dorm, [])

    def test_without_counterpart_only_past_the_longer_limit(self):
        e = [app("solo/windows", "Solo", "1.0", "Acme.Solo")]
        tree = {"manifests/a/Acme/Solo": ["1.0"]}
        *_, quiet = run(e, FakeWinget(tree, {"manifests/a/Acme/Solo/1.0": "2026-01-01"}))  # 319 days: past 180, under 365
        self.assertEqual(quiet, [])
        _, dorm, *_, quiet = run(e, FakeWinget(tree, {"manifests/a/Acme/Solo/1.0": "2024-11-01"}))
        self.assertEqual((dorm, [x["signal"] for x in quiet]), ([], ["no macOS counterpart"]))

    def test_macos_manifest_change_without_a_newer_version_is_not_dormant(self):
        # aircall: 3.1.66 on both; the macOS manifest changed (scripts), the version didn't
        e = [app("aircall/windows", "Aircall", "3.1.66", "Aircall.Aircall"), app("aircall/darwin", "Aircall", "3.1.66")]
        w = FakeWinget({"manifests/a/Aircall/Aircall": ["3.1.66"]}, {"manifests/a/Aircall/Aircall/3.1.66": "2025-01-23"})
        _, dorm, *_ = run(e, w, {"aircall/darwin": "2026-11-01"})
        self.assertEqual(dorm, [])


class Drift(unittest.TestCase):
    def test_tableau_publisher_is_not_what_the_query_matches(self):  # #58817
        p = "manifests/t/Tableau/Desktop"
        w = FakeWinget({p: ["24.3.746", "24.3.965"]},
                       text={f"{p}/24.3.965/Tableau.Desktop.locale.en-US.yaml": locale("Tableau Software, LLC"),
                             f"{p}/24.3.746/Tableau.Desktop.locale.en-US.yaml": locale("Tableau Software, LLC")})
        _, _, drift, *_ = run([app("tableau-desktop/windows", "Tableau Desktop", "24.3.965", "Tableau.Desktop", "Salesforce, Inc")], w)
        self.assertEqual([(x["winget_publisher"], x["versions"]) for x in drift], [("Tableau Software, LLC", ["24.3.965", "24.3.746"])])

    def test_legal_suffixes_and_punctuation_dont_count(self):
        p = "manifests/j/JetBrains/PyCharm"
        w = FakeWinget({p: ["2026.3"]}, text={f"{p}/2026.3/JetBrains.PyCharm.locale.en-US.yaml": locale("JetBrains s.r.o")})
        _, _, drift, *_ = run([app("pycharm/windows", "PyCharm", "2026.3", "JetBrains.PyCharm", "JetBrains s.r.o.")], w)
        self.assertEqual(drift, [])
        self.assertEqual(W.norm_pub("Tableau Software, LLC"), "tableau software")
        self.assertEqual(W.norm_pub("The WinDirStat Team"), W.norm_pub("WinDirStat Team"))
        self.assertEqual(W.norm_pub("The Wireshark developer community, https://www.wireshark.org/"),
                         W.norm_pub("The Wireshark developer community, https://www.wireshark.org"))

    def test_registered_publisher_from_the_installer_manifest(self):
        p = "manifests/a/Acme/App"
        inst = "Installers:\n- Architecture: x64\n  AppsAndFeaturesEntries:\n  - Publisher: Acme Old Name\n"
        w = FakeWinget({p: ["2.0"]}, text={f"{p}/2.0/Acme.App.installer.yaml": inst,
                                           f"{p}/2.0/Acme.App.locale.en-US.yaml": locale("Acme Corp")})
        _, _, drift, *_ = run([app("app/windows", "App", "2.0", "Acme.App", "Acme Corp")], w)
        self.assertEqual([(x["winget_publisher"], x["source"]) for x in drift], [("Acme Old Name", "registered")])

    def test_no_program_publisher_means_no_check(self):
        p = "manifests/a/Acme/App"
        w = FakeWinget({p: ["2.0"]}, text={f"{p}/2.0/Acme.App.locale.en-US.yaml": locale("Someone Else")})
        _, _, drift, *_ = run([app("app/windows", "App", "2.0", "Acme.App")], w)
        self.assertEqual(drift, [])


if __name__ == "__main__":
    unittest.main()
