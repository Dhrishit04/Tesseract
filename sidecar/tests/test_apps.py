"""Finding installed apps by name and opening them, without touching the real desktop."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import apps
from skills.app_launcher import AppLauncherLaunchSkill, AppLauncherListAppsSkill

START_APPS = [
    {"Name": "Excel", "AppID": "Microsoft.Office.EXCEL.EXE.15"},
    {"Name": "Excel Viewer", "AppID": "viewer"},
    {"Name": "Word", "AppID": "Microsoft.Office.WINWORD.EXE.15"},
    {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {"Name": "Paint 3D", "AppID": "paint3d"},
    {"Name": "Paint", "AppID": "paint"},
    {"Name": "Notepad++", "AppID": "npp"},
    {"Name": "Notepad", "AppID": "notepad"},
    {"Name": "Sticky Notes", "AppID": "sticky"},
    {"Name": "Snipping Tool", "AppID": "snip"},
    {"Name": "Snapchat", "AppID": "snap"},
]


class ListAppsTests(unittest.TestCase):
    def setUp(self):
        apps._cache = None
        self.addCleanup(setattr, apps, "_cache", None)

    def powershell(self, output):
        return patch.object(apps.subprocess, "run", return_value=SimpleNamespace(stdout=output))

    def test_parses_start_apps(self):
        with patch.object(apps.os, "name", "nt"), self.powershell(json.dumps(START_APPS)):
            listed = apps.list_apps()
        self.assertIn({"name": "Excel", "app_id": "Microsoft.Office.EXCEL.EXE.15"}, listed)

    def test_a_single_app_comes_back_as_one_object(self):
        with patch.object(apps.os, "name", "nt"), self.powershell(json.dumps(START_APPS[0])):
            self.assertEqual(len(apps.list_apps()), 1)

    def test_the_list_is_kept_for_a_minute(self):
        with patch.object(apps.os, "name", "nt"), self.powershell(json.dumps(START_APPS)) as run:
            apps.list_apps()
            apps.list_apps()
        self.assertEqual(run.call_count, 1)

    def test_powershell_failure_is_an_empty_list(self):
        with patch.object(apps.os, "name", "nt"), patch.object(apps.subprocess, "run", side_effect=OSError("no")):
            self.assertEqual(apps.list_apps(), [])

    def test_never_uses_a_shell(self):
        with patch.object(apps.os, "name", "nt"), self.powershell("[]") as run:
            apps.list_apps()
        self.assertNotIn("shell", run.call_args.kwargs)


class ResolveAppTests(unittest.TestCase):
    def setUp(self):
        listed = [{"name": a["Name"], "app_id": a["AppID"]} for a in START_APPS]
        patcher = patch.object(apps, "list_apps", return_value=listed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_exact_name_beats_partial_matches(self):
        self.assertEqual(apps.resolve_app("excel")["app_id"], "Microsoft.Office.EXCEL.EXE.15")

    def test_one_partial_match_is_used(self):
        self.assertEqual(apps.resolve_app("calc")["name"], "Calculator")

    def test_an_ambiguous_name_returns_the_candidates(self):
        result = apps.resolve_app("sn")
        self.assertEqual({app["name"] for app in result["candidates"]}, {"Snipping Tool", "Snapchat"})

    def test_two_apps_with_the_same_name_are_not_guessed(self):
        twins = [{"name": "Excel", "app_id": "desktop"}, {"name": "Excel", "app_id": "web"}]
        with patch.object(apps, "list_apps", return_value=twins):
            self.assertEqual(apps.resolve_app("excel"), {"candidates": twins})

    def test_an_app_id_picks_exactly_that_app(self):
        twins = [{"name": "Excel", "app_id": "desktop"}, {"name": "Excel", "app_id": "web"}]
        with patch.object(apps, "list_apps", return_value=twins):
            self.assertEqual(apps.resolve_app("web")["app_id"], "web")

    def test_an_unknown_name_is_none(self):
        self.assertIsNone(apps.resolve_app("no such app"))


class LaunchTests(unittest.TestCase):
    def run_launch(self, target):
        return asyncio.run(AppLauncherLaunchSkill().execute(target=target))

    def setUp(self):
        self.opened = []
        self.patches = [
            patch("skills.app_launcher.platform.system", return_value="Windows"),
            patch("skills.app_launcher.resolve_app", side_effect=lambda name: {"excel": {"name": "Excel", "app_id": "X!App"},
                                                                               "sn": {"candidates": [{"name": "Snap", "app_id": "a"}]}}.get(name)),
            patch("skills.app_launcher.open_app", side_effect=self.opened.append),
            patch("skills.app_launcher.visible_windows", return_value={1: "Old"}),
            patch("skills.app_launcher.wait_for_new_windows", return_value=[{"handle": 2, "title": "Book1 - Excel"}]),
            patch("skills.app_launcher.open_target"),
            patch("skills.app_launcher.shutil.which", side_effect=lambda name: r"C:\Windows\winver.exe" if name == "winver" else None),
        ]
        self.open_target = [p.start() for p in self.patches][-2]
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_opens_an_app_by_name_and_reports_its_window(self):
        result = self.run_launch("excel")
        self.assertEqual(self.opened, ["X!App"])
        self.assertEqual(result.data["windows"], [{"handle": 2, "title": "Book1 - Excel"}])

    def test_an_ambiguous_name_opens_nothing(self):
        result = self.run_launch("sn")
        self.assertFalse(result.success)
        self.assertIn("Snap (a)", result.error)
        self.assertEqual(self.opened, [])

    def test_a_program_on_the_path_opens_through_the_shell(self):
        self.assertTrue(self.run_launch("winver").success)
        self.open_target.assert_called_once_with(r"C:\Windows\winver.exe")

    def test_a_name_nothing_knows_is_a_plain_error(self):
        result = self.run_launch("nothing-called-this")
        self.assertFalse(result.success)
        self.assertIn("No app", result.error)
        self.open_target.assert_not_called()

    def test_a_url_is_not_looked_up_as_an_app(self):
        self.assertTrue(self.run_launch("https://example.com").success)
        self.open_target.assert_called_once_with("https://example.com")

    def test_shell_folders_are_refused(self):
        for target in ("shell:Profile\\TESSER~1", "SHELL:AppData"):
            with self.subTest(target=target):
                self.assertFalse(self.run_launch(target).success)
        self.open_target.assert_not_called()

    def test_scripts_are_refused(self):
        with tempfile.TemporaryDirectory(dir=".") as folder:
            for name in ("a.bat", "a.VBS", "a.hta", "a.ps1", "a.url"):
                script = Path(folder) / name
                script.write_text("x")
                with self.subTest(name=name):
                    self.assertFalse(self.run_launch(str(script.resolve())).success)
        self.open_target.assert_not_called()

    def test_tesseracts_data_is_refused_before_any_lookup(self):
        self.assertFalse(self.run_launch("excel .tesseract").success)
        self.assertEqual(self.opened, [])

    def test_the_approval_card_names_the_target(self):
        self.assertIn("excel", AppLauncherLaunchSkill().approval_summary({"target": "excel"}))


class OpenTargetTests(unittest.TestCase):
    def test_the_shell_opens_it_so_it_doesnt_die_with_tesseract(self):
        with patch.object(apps.subprocess, "Popen") as popen:
            apps.open_target(r"C:\a,b\file name.txt")
        popen.assert_called_once_with('explorer.exe "C:\\a,b\\file name.txt"')

    def test_a_quote_cant_add_arguments(self):
        with patch.object(apps.subprocess, "Popen") as popen, self.assertRaises(ValueError):
            apps.open_target('x" /e,"C:\\')
        popen.assert_not_called()


class WaitForWindowTests(unittest.TestCase):
    def test_it_waits_for_windows_to_settle(self):
        # A splash screen comes first, then the real window.
        snapshots = iter([{1: "Old", 2: "Splash"}, {1: "Old", 2: "Splash", 3: "Book1"}])
        with patch.object(apps, "visible_windows", side_effect=lambda: next(snapshots)), patch.object(apps.time, "sleep"):
            found = apps.wait_for_new_windows({1: "Old"}, timeout=1, settle=1, poll=0)
        self.assertEqual([window["handle"] for window in found], [2, 3])

    def test_nothing_when_no_window_appears(self):
        with patch.object(apps, "visible_windows", return_value={1: "Old"}):
            self.assertEqual(apps.wait_for_new_windows({1: "Old"}, timeout=0, poll=0), [])


class ListAppsSkillTests(unittest.TestCase):
    def test_filters_by_query(self):
        with patch("skills.app_launcher.list_apps", return_value=[{"name": "Excel", "app_id": "x"}, {"name": "Word", "app_id": "w"}]):
            result = asyncio.run(AppLauncherListAppsSkill().execute(query="exc"))
        self.assertEqual([app["name"] for app in result.data["apps"]], ["Excel"])

    def test_only_reads(self):
        self.assertEqual(AppLauncherListAppsSkill.kind, "read")


if __name__ == "__main__":
    unittest.main()
