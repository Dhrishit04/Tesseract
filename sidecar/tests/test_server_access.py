"""Who may call the sidecar, and the settings endpoints."""

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
        self.client = TestClient(server.app, base_url="http://127.0.0.1")

    def tearDown(self):
        self.home_patch.stop()
        self.home.cleanup()

    def test_dns_rebinding_host_is_refused(self):
        # A rebinding page reaches 127.0.0.1 under its own name, and would otherwise read the API key.
        response = self.client.get("/settings", headers={"Host": "evil.example:8765"})

        self.assertEqual(response.status_code, 400)

    def test_local_hosts_are_allowed(self):
        for host in ("127.0.0.1:8765", "localhost:8765"):
            self.assertEqual(self.client.get("/health", headers={"Host": host}).status_code, 200)

    def test_post_from_another_website_is_refused(self):
        response = self.client.post("/google/auth/disconnect", headers={"Origin": "https://evil.example"})

        self.assertEqual(response.status_code, 403)

    def test_post_from_the_app_is_allowed(self):
        response = self.client.post("/settings/reload", headers={"Origin": "http://tauri.localhost"})

        self.assertEqual(response.status_code, 200)

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
        self.client = TestClient(server.app, base_url="http://127.0.0.1")

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
