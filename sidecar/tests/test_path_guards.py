"""Tools must refuse to touch anything outside what the user allowed."""

import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from automation.browser import WebAutomation
from skills.app_launcher import AppLauncherLaunchSkill
from skills.filesystem import FilesystemReadFileSkill, FilesystemWriteFileSkill
from skills.gmail import _extract_body


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.workspace = Path(self.root.name) / "work"
        self.workspace.mkdir()
        self.previous_cwd = os.getcwd()
        os.chdir(self.workspace)

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
