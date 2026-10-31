import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.orchestrator import AutomationOrchestrator
from skills.registry import SkillRegistry
from skills.web_automation import _get_orchestrator


class Phase11IntegrationTests(unittest.TestCase):
    def test_registry_exposes_unified_automation_tools(self):
        registry = SkillRegistry()
        skills = registry.load_skills()

        for skill_name in (
            "web_automation.navigate",
            "desktop_automation.find_window",
            "office.read_document",
        ):
            self.assertIn(skill_name, skills)

    def test_web_skills_use_process_global_orchestrator(self):
        from automation.orchestrator import get_automation_orchestrator

        self.assertIs(_get_orchestrator(), get_automation_orchestrator())

    def test_disabled_policy_blocks_web_actions(self):
        orchestrator = AutomationOrchestrator()

        with patch(
            "automation.orchestrator.load_automation_policy",
            return_value={"automation_enabled": False, "ocr_enabled": False},
        ):
            result = asyncio.run(orchestrator.execute_web_action("get_page_content"))

        self.assertFalse(result["success"])
        self.assertIn("disabled", result["error"])

    def test_policy_reads_tesseract_settings(self):
        with tempfile.TemporaryDirectory() as home:
            settings_path = Path(home) / ".tesseract" / "settings.json"
            settings_path.parent.mkdir()
            settings_path.write_text(
                json.dumps({"automation": {"automation_enabled": True, "ocr_enabled": True}}),
                encoding="utf-8",
            )
            with patch("os.path.expanduser", return_value=home):
                from automation.policy import load_automation_policy

                self.assertEqual(
                    load_automation_policy(),
                    {"automation_enabled": True, "ocr_enabled": True, "share_screenshots": False},
                )


if __name__ == "__main__":
    unittest.main()
