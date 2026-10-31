"""Tools must refuse to touch anything outside what the user allowed."""

import asyncio
import base64
import json
import os
import tempfile
import unittest
try:
    import _winapi
except ImportError:
    _winapi = None
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

from automation.browser import WebAutomation
from skills.app_launcher import AppLauncherLaunchSkill, _refusal
from automation.office import OfficeAutomation, OfficeAutomationError
from file_access import _folded, access_level, is_protected, known_folders
from skills.filesystem import FilesystemListDirSkill, FilesystemReadFileSkill, FilesystemSearchSkill, FilesystemWriteFileSkill
from skills.gmail import _extract_body


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.workspace = Path(self.root.name) / "work"
        self.workspace.mkdir()
        self.previous_cwd = os.getcwd()
        os.chdir(self.workspace)
        # The temp folder sits inside the real profile, so point the profile elsewhere.
        self.home = Path(self.root.name) / "home"
        self.downloads = Path(self.root.name) / "other-drive" / "Downloads"
        self.vault_folder = self.home / ".tesseract"
        self.vault_folder.mkdir(parents=True)
        self.downloads.mkdir(parents=True)
        (self.vault_folder / "secrets.enc").write_text("vault", encoding="utf-8")
        home = patch.dict(os.environ, {"USERPROFILE": str(self.home), "HOME": str(self.home)})
        folders = patch("file_access.known_folders", return_value={"Downloads": self.downloads})
        home.start()
        folders.start()
        self.addCleanup(home.stop)
        self.addCleanup(folders.stop)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.root.cleanup()

    def test_write_refuses_sibling_folder_sharing_the_workspace_prefix(self):
        result = asyncio.run(FilesystemWriteFileSkill().execute(path="../work-evil/x.txt", content="x"))

        self.assertFalse(result.success)
        self.assertFalse((Path(self.root.name) / "work-evil").exists())

    def test_write_inside_workspace_works(self):
        result = asyncio.run(FilesystemWriteFileSkill().execute(path="notes/a.txt", content="hi"))

        self.assertTrue(result.success, result.error)
        self.assertEqual((self.workspace / "notes" / "a.txt").read_text(encoding="utf-8"), "hi")

    def test_read_refuses_parent_folder(self):
        (Path(self.root.name) / "secret.txt").write_text("key", encoding="utf-8")

        result = asyncio.run(FilesystemReadFileSkill().execute(path="../secret.txt"))

        self.assertFalse(result.success)

    def test_reaches_the_profile_and_folders_on_other_drives(self):
        for folder in (self.home / "Documents", self.downloads):
            folder.mkdir(exist_ok=True)
            (folder / "a.txt").write_text("hi", encoding="utf-8")
            result = asyncio.run(FilesystemReadFileSkill().execute(path=str(folder / "a.txt")))
            self.assertTrue(result.success, result.error)

    def test_never_reaches_tesseracts_own_data(self):
        vault = self.vault_folder / "secrets.enc"
        for path in (vault, self.home / "x" / ".." / ".tesseract" / "secrets.enc", str(vault).replace(".tesseract", ".TESSERACT"),
                     str(vault).replace(".tesseract", ".tesseract.")):
            with self.subTest(path=path):
                self.assertFalse(asyncio.run(FilesystemReadFileSkill().execute(path=str(path))).success)
        self.assertFalse(asyncio.run(FilesystemListDirSkill().execute(path=str(self.vault_folder))).success)
        self.assertFalse(asyncio.run(FilesystemWriteFileSkill().execute(path=str(vault), content="x")).success)
        self.assertEqual(vault.read_text(encoding="utf-8"), "vault")

    def test_search_skips_tesseracts_own_data(self):
        (self.home / "notes.enc").write_text("hi", encoding="utf-8")
        result = asyncio.run(FilesystemSearchSkill().execute(pattern="**/*.enc", path=str(self.home)))
        self.assertEqual(result.data["matches"], ["notes.enc"])

    def test_office_follows_the_same_rule(self):
        office = OfficeAutomation()
        office._ensure_allowed(self.downloads / "book.xlsx")
        with self.assertRaises(OfficeAutomationError):
            office._ensure_allowed(self.vault_folder / "book.xlsx")
        with self.assertRaises(OfficeAutomationError):
            office._ensure_allowed(Path(self.root.name) / "elsewhere" / "book.xlsx")

    def set_level(self, level):
        (self.vault_folder / "settings.json").write_text(json.dumps({"file_access": level}), encoding="utf-8")

    def can_read(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("hi", encoding="utf-8")
        return asyncio.run(FilesystemReadFileSkill().execute(path=str(path))).success

    def test_each_level_reaches_what_it_says(self):
        other_drive = Path(self.root.name) / "elsewhere" / "a.txt"
        profile = self.home / "Music" / "a.txt"
        downloads = self.downloads / "a.txt"
        working = self.workspace / "a.txt"
        expected = {
            "full": (True, True, True, True),
            "profile": (False, True, True, True),
            "personal": (False, False, True, True),
            "workspace": (False, False, False, True),
        }
        for level, reachable in expected.items():
            self.set_level(level)
            for path, allowed in zip((other_drive, profile, downloads, working), reachable):
                with self.subTest(level=level, path=path):
                    self.assertEqual(self.can_read(path), allowed)

    def test_no_level_reaches_tesseracts_own_data(self):
        for level in ("full", "profile", "personal", "workspace"):
            self.set_level(level)
            with self.subTest(level=level):
                self.assertFalse(self.can_read(self.vault_folder / "secrets.enc"))

    def test_the_full_level_refuses_other_spellings_of_tesseracts_data(self):
        self.set_level("full")
        vault = str(self.vault_folder / "secrets.enc")
        for path in (vault.replace(".tesseract", ".TESSERACT"), vault.replace(".tesseract", ".tesseract."),
                     vault.replace(".tesseract", ".tesseract::$INDEX_ALLOCATION"), vault + "::$DATA"):
            with self.subTest(path=path):
                self.assertFalse(asyncio.run(FilesystemReadFileSkill().execute(path=path)).success)

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_a_network_path_to_this_machine_is_the_same_folder(self):
        vault = self.vault_folder / "secrets.enc"
        share = "\\\\localhost\\" + str(vault.resolve()).replace(":", "$", 1)
        if not os.path.exists(share):
            self.skipTest("admin share not available")
        self.assertTrue(is_protected(Path(share)))

    def test_a_link_into_tesseracts_data_is_refused(self):
        link = self.home / "shortcut"
        if os.name == "nt":
            _winapi.CreateJunction(str(self.vault_folder), str(link))  # needs no admin rights, unlike a symlink
        else:
            link.symlink_to(self.vault_folder, target_is_directory=True)
        self.set_level("full")
        self.assertFalse(asyncio.run(FilesystemReadFileSkill().execute(path=str(link / "secrets.enc"))).success)

    def test_another_name_for_the_same_folder_is_refused(self):
        alias = Path(self.root.name) / "alias"
        with patch("os.path.samefile", side_effect=lambda candidate, data: Path(candidate) == alias.resolve()):
            self.assertTrue(is_protected(alias / "secrets.enc"))

    def test_a_folder_that_only_starts_the_same_is_not_protected(self):
        self.assertFalse(is_protected(self.home / ".tesseract-backup" / "a.txt"))

    def test_an_unknown_level_gets_the_narrowest_access(self):
        for level in ("everything", ["full"], None):
            self.set_level(level)
            with self.subTest(level=level):
                self.assertEqual(access_level(), "workspace")

    def test_no_saved_level_keeps_the_profile(self):
        self.assertEqual(access_level(), "profile")

    def test_office_follows_the_chosen_level(self):
        self.set_level("workspace")
        with self.assertRaises(OfficeAutomationError):
            OfficeAutomation()._ensure_allowed(self.downloads / "book.xlsx")

    def test_open_refuses_what_the_level_refuses(self):
        self.set_level("workspace")
        document = self.downloads / "a.txt"
        document.write_text("hi", encoding="utf-8")
        program = self.downloads / "tool.exe"
        program.write_text("", encoding="utf-8")
        vault = self.vault_folder / "secrets.enc"
        for target in (str(document), str(vault), "shell:Profile\\.tesseract\\secrets.enc", "file:///" + str(document)):
            with self.subTest(target=target):
                self.assertTrue(_refusal(target))
        for target in ("notepad", "https://example.com", "ms-settings:", str(program)):
            with self.subTest(target=target):
                self.assertIsNone(_refusal(target))

    def test_a_configured_office_root_still_narrows_office(self):
        office = OfficeAutomation(allowed_root=self.downloads)
        office._ensure_allowed(self.downloads / "book.xlsx")
        with self.assertRaises(OfficeAutomationError):
            office._ensure_allowed(self.home / "book.xlsx")


class MacPathTests(unittest.TestCase):
    def test_a_case_insensitive_disk_sees_one_folder(self):
        home = PurePosixPath("/Users/me/.tesseract/secrets.enc")
        self.assertEqual(_folded(PurePosixPath("/users/ME/.TESSERACT/SECRETS.ENC")), _folded(home))

    def test_macos_decomposed_names_match(self):
        self.assertEqual(_folded(PurePosixPath("/Users/Jose\u0301")), _folded(PurePosixPath("/Users/Jos\u00e9")))


class KnownFolderTests(unittest.TestCase):
    def test_folders_are_real_paths(self):
        folders = known_folders()
        self.assertEqual(set(folders), {"Desktop", "Documents", "Downloads"})
        self.assertTrue(all("%" not in str(folder) for folder in folders.values()))


class BrowserGuardTests(unittest.TestCase):
    def test_navigate_refuses_local_files(self):
        browser = WebAutomation()
        browser._page = MagicMock(goto=AsyncMock())

        with self.assertRaises(ValueError):
            asyncio.run(browser.navigate("file:///C:/Users/me/.tesseract/settings.json"))
        browser._page.goto.assert_not_called()


class AppLauncherGuardTests(unittest.TestCase):
    def test_launch_asks_the_owner_first(self):
        # Launching a program is a change, so the chat loop asks before it runs.
        self.assertEqual(AppLauncherLaunchSkill.kind, "change")


class GmailBodyTests(unittest.TestCase):
    def test_html_only_message_body_is_returned(self):
        data = base64.urlsafe_b64encode(b"<p>Your receipt</p>").decode()

        self.assertEqual(_extract_body({"mimeType": "text/html", "body": {"data": data}}), "<p>Your receipt</p>")


if __name__ == "__main__":
    unittest.main()
