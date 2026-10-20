"""Skill discovery must work from source and from a frozen (PyInstaller) build."""

import types
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.registry import SkillRegistry


class SkillDiscoveryTests(unittest.TestCase):
    def test_discovers_skills_when_no_source_files_exist(self):
        # A PyInstaller one-file build ships bytecode in an archive, so globbing *.py finds nothing.
        with patch.object(Path, "glob", return_value=iter(())):
            modules = SkillRegistry().discover_skills()

        self.assertIn("filesystem", modules)
        self.assertIn("gmail", modules)
        self.assertNotIn("registry", modules)
        self.assertNotIn("skill_base", modules)


class SkillLoadingTests(unittest.TestCase):
    def test_broken_skill_is_logged_and_imported_bases_are_skipped(self):
        module = types.ModuleType("skills.fake")
        exec(
            "from skills.skill_base import BlockingSkill, SkillBase\n"
            "class Broken(SkillBase):\n"
            "    name = 'fake.broken'\n",  # no description, parameters or execute
            module.__dict__,
        )
        registry = SkillRegistry()

        with patch("importlib.import_module", return_value=module), self.assertLogs("skills.registry", "ERROR") as logs:
            registry._load_skill_module("fake")

        self.assertEqual(registry.get_all_skills(), {})
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Broken", logs.output[0])


if __name__ == "__main__":
    unittest.main()
