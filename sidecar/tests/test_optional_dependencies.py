"""The sidecar must start with only the base dependencies installed."""

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

SIDECAR_DIR = Path(__file__).resolve().parent.parent

# Simulates an install without the [google] and [automation] extras.
BOOT_WITHOUT_EXTRAS = textwrap.dedent(
    """
    import sys
    for blocked in ("google", "googleapiclient", "google_auth_oauthlib", "playwright", "pywinauto", "pytesseract", "pyautogui"):
        sys.modules[blocked] = None
    from fastapi.testclient import TestClient
    import server
    client = TestClient(server.app, base_url="http://127.0.0.1")
    assert client.get("/health").json()["status"] == "ok"
    status = client.get("/google/auth/status").json()
    assert status["connected"] is False and "not installed" in status["error"], status
    skills = [skill["name"] for skill in client.get("/skills").json()["skills"]]
    assert "filesystem.read_file" in skills, skills
    print("booted")
    """
)


class OptionalDependencyTests(unittest.TestCase):
    def test_server_boots_without_google_or_automation_extras(self):
        result = subprocess.run(
            [sys.executable, "-c", BOOT_WITHOUT_EXTRAS],
            cwd=SIDECAR_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("booted", result.stdout)


if __name__ == "__main__":
    unittest.main()
