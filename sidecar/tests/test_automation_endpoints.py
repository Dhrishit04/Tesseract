"""HTTP contract of the automation, settings and skills endpoints."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class SidecarEndpointTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.home.name) / ".tesseract" / "settings.json"
        self.home_patch = patch("os.path.expanduser", return_value=self.home.name)
        self.home_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.home_patch.stop()
        self.home.cleanup()

    def write_settings(self, settings: dict) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")

    def test_web_start_refused_while_automation_disabled(self):
        response = self.client.post("/automation/web/start", json={"url": "https://example.com"})

        self.assertEqual(response.json(), {"success": False, "error": "Automation is disabled in settings"})

    def test_web_action_requires_started_session(self):
        response = self.client.post("/automation/web/click", json={"selector": "#go"})

        self.assertFalse(response.json()["success"])
        self.assertIn("not started", response.json()["error"])

    def test_desktop_action_requires_started_session(self):
        response = self.client.post("/automation/desktop/find-window", json={"title": "Notepad"})

        self.assertFalse(response.json()["success"])
        self.assertIn("not started", response.json()["error"])

    def test_office_refused_while_automation_disabled(self):
        response = self.client.post("/automation/office/read-doc", json={"path": "a.docx"})

        self.assertEqual(response.json(), {"success": False, "error": "Automation is disabled in settings"})

    def test_automation_settings_update_keeps_other_sections(self):
        self.write_settings({"openrouter_api_key": "sk-keep", "automation": {"automation_enabled": False}})

        self.client.post("/automation/settings", json={"settings": {"automation_enabled": True}})

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["openrouter_api_key"], "sk-keep")
        self.assertTrue(saved["automation"]["automation_enabled"])

    def test_voice_settings_update_keeps_other_sections(self):
        self.write_settings({"openrouter_api_key": "sk-keep"})

        self.client.post("/voice/settings", json={"settings": {"voice_enabled": True}})

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["openrouter_api_key"], "sk-keep")
        self.assertTrue(saved["voice"]["voice_enabled"])

    def test_execute_skill_accepts_api_safe_tool_name(self):
        response = self.client.post(
            "/skills/execute",
            json={"skill_name": "filesystem__list_directory", "parameters": {"path": "."}},
        )

        self.assertNotIn("not found", str(response.json().get("error")))

    def test_packaged_app_origin_is_allowed(self):
        for origin in ("http://tauri.localhost", "tauri://localhost"):
            response = self.client.options(
                "/health",
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
            )
            self.assertEqual(response.headers.get("access-control-allow-origin"), origin)


class SettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.home.name) / ".tesseract" / "settings.json"
        self.home_patch = patch("os.path.expanduser", return_value=self.home.name)
        self.home_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.home_patch.stop()
        self.home.cleanup()

    def test_put_settings_saves_and_reconfigures_client(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps({"automation": {"office_root": "C:/Docs"}}), encoding="utf-8")

        response = self.client.put("/settings", json={"openrouter_api_key": "sk-new", "automation": {"automation_enabled": True}})

        self.assertTrue(response.json()["success"])
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["automation"], {"office_root": "C:/Docs", "automation_enabled": True})
        self.assertEqual(server.get_client().api_key, "sk-new")

    def test_reload_picks_up_file_written_by_desktop_app(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps({"openrouter_api_key": "sk-from-tauri"}), encoding="utf-8")

        with patch.object(server, "_client", server.OpenRouterClient(api_key="sk-stale")):
            self.client.post("/settings/reload")

            self.assertEqual(server.get_client().api_key, "sk-from-tauri")


if __name__ == "__main__":
    unittest.main()
