"""Skill discovery must work from source and from a frozen (PyInstaller) build."""

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


if __name__ == "__main__":
    unittest.main()
