"""Every tool is read or change, as the spec's autonomy rule says (read freely, ask before any change)."""

import unittest
from unittest.mock import patch

from skills.registry import SkillRegistry
from skills.skill_base import SkillBase, SkillResult

READ = {
    "filesystem.read_file", "filesystem.list_directory", "filesystem.search",
    "gmail.read_inbox", "gmail.search", "gmail.get_thread",
    "calendar.list_events", "calendar.check_schedule",
    "web_automation.get_page_content", "web_automation.extract", "web_automation.screenshot",
    "web_automation.wait_for", "web_automation.close",
    "desktop_automation.list_windows", "desktop_automation.get_elements", "desktop_automation.get_text",
    "desktop_automation.screenshot", "desktop_automation.ocr", "desktop_automation.close",
    "office.read_document", "office.read_excel", "app_launcher.list_windows",
}
IRREVERSIBLE = {"gmail.send_email", "calendar.delete_event"}
AUTOMATION_PREFIXES = ("web_automation__", "desktop_automation__", "office__")


class Unclassified(SkillBase):
    name = "test.unclassified"
    description = "A skill that declares no kind."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return SkillResult.success()


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skills = SkillRegistry().load_skills()

    def test_every_skill_is_classified_as_the_spec_says(self):
        self.assertTrue(READ <= set(self.skills), READ - set(self.skills))
        for name, skill in self.skills.items():
            self.assertEqual(skill.kind, "read" if name in READ else "change", name)

    def test_only_sending_email_and_deleting_events_are_irreversible(self):
        self.assertEqual({n for n, s in self.skills.items() if s.irreversible}, IRREVERSIBLE)

    def test_every_change_skill_has_a_plain_title(self):
        for name, skill in self.skills.items():
            if skill.kind == "change":
                self.assertTrue(skill.approval_title and "." not in skill.approval_title, name)

    def test_send_email_summary_names_recipient_and_subject(self):
        summary = self.skills["gmail.send_email"].approval_summary(
            {"to": "priya@example.com", "subject": "Lunch", "body": "x"}
        )
        self.assertIn("priya@example.com", summary)
        self.assertIn("Lunch", summary)

    def test_write_file_summary_names_path_and_size(self):
        summary = self.skills["filesystem.write_file"].approval_summary({"path": "notes.txt", "content": "hello"})
        self.assertEqual(summary, "Write 5 characters to notes.txt")

    def test_unclassified_skill_defaults_to_change(self):
        skill = Unclassified()
        self.assertEqual(skill.kind, "change")
        self.assertFalse(skill.irreversible)
        self.assertEqual(skill.approval_summary({}), "A skill that declares no kind.")


class AutomationToggleTests(unittest.TestCase):
    def tool_names(self, enabled: bool) -> set[str]:
        policy = {"automation_enabled": enabled, "ocr_enabled": False}
        with patch("skills.registry.load_automation_policy", return_value=policy):
            registry = SkillRegistry()
            registry.load_skills()
            return {d["function"]["name"] for d in registry.get_tool_definitions()}

    def test_automation_tools_hidden_when_automation_off(self):
        names = self.tool_names(False)
        self.assertFalse([n for n in names if n.startswith(AUTOMATION_PREFIXES)])
        self.assertIn("filesystem__read_file", names)

    def test_automation_tools_listed_when_automation_on(self):
        names = self.tool_names(True)
        self.assertIn("web_automation__navigate", names)
        self.assertIn("office__read_document", names)


if __name__ == "__main__":
    unittest.main()
