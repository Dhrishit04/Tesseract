"""Skill registry with auto-discovery from the skills/ directory."""

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Any

from skills.skill_base import SkillBase

logger = logging.getLogger(__name__)

# OpenAI-compatible APIs only accept [a-zA-Z0-9_-] in function names, while
# skills are namespaced with dots ("filesystem.read_file").
TOOL_NAME_SEPARATOR = "__"


def to_tool_name(skill_name: str) -> str:
    """Return the API-safe function name for a skill."""
    return skill_name.replace(".", TOOL_NAME_SEPARATOR)


class SkillRegistry:
    """Registry for discovering, loading, and managing skills."""

    def __init__(self, skills_dir: Path | None = None):
        """Initialize the skill registry.

        Args:
            skills_dir: Path to the skills directory. Defaults to sidecar/skills/
        """
        if skills_dir is None:
            # Default to sidecar/skills/ relative to this file
            self.skills_dir = Path(__file__).parent
        else:
            self.skills_dir = Path(skills_dir)

        self._skills: dict[str, SkillBase] = {}
        self._loaded = False

    def discover_skills(self) -> list[str]:
        """Discover all Python files in the skills directory that could be skills.

        Returns:
            List of module names (without .py extension) that look like skills
        """
        skill_modules = []
        for py_file in self.skills_dir.glob("*.py"):
            # Skip __init__.py, skill_base.py, registry.py, and private modules
            if py_file.name.startswith("_") or py_file.name in {
                "__init__.py",
                "skill_base.py",
                "registry.py",
            }:
                continue
            skill_modules.append(py_file.stem)
        return skill_modules

    def load_skills(self) -> dict[str, SkillBase]:
        """Load all discovered skills into the registry.

        Returns:
            Dictionary mapping skill name to skill instance
        """
        if self._loaded:
            return self._skills

        for module_name in self.discover_skills():
            try:
                self._load_skill_module(module_name)
            except Exception as e:
                logger.error(f"Failed to load skill module '{module_name}': {e}")

        self._loaded = True
        logger.info(f"Loaded {len(self._skills)} skills: {list(self._skills.keys())}")
        return self._skills

    def _load_skill_module(self, module_name: str) -> None:
        """Load a single skill module and register any SkillBase subclasses."""
        module_path = f"skills.{module_name}"

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            logger.error(f"Failed to import skill module '{module_name}': {e}")
            return

        # Find all SkillBase subclasses in the module
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is SkillBase:
                continue
            if issubclass(obj, SkillBase):
                try:
                    instance = obj()
                    if instance.name in self._skills:
                        logger.warning(
                            f"Skill '{instance.name}' already registered, skipping duplicate from {module_name}"
                        )
                        continue
                    self._skills[instance.name] = instance
                    logger.info(f"Registered skill: {instance.name}")
                except Exception as e:
                    logger.error(f"Failed to instantiate skill '{name}' from {module_name}: {e}")

    def get_skill(self, name: str) -> SkillBase | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_all_skills(self) -> dict[str, SkillBase]:
        """Get all registered skills."""
        return self._skills.copy()

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get all skills as OpenAI-compatible tool definitions."""
        definitions = []
        for skill in self._skills.values():
            definition = skill.to_tool_definition()
            definition["function"]["name"] = to_tool_name(skill.name)
            definitions.append(definition)
        return definitions

    def resolve_skill_name(self, name: str) -> str:
        """Map an API-safe tool name back to its registered skill name."""
        if name in self._skills:
            return name
        for skill_name in self._skills:
            if to_tool_name(skill_name) == name:
                return skill_name
        return name

    async def execute_skill(self, skill_name: str, **kwargs: Any) -> Any:
        """Execute a skill by name with the given parameters.

        Args:
            skill_name: Skill name
            **kwargs: Parameters for the skill

        Returns:
            SkillResult from the skill execution
        """
        skill = self.get_skill(skill_name)
        if not skill:
            from skills.skill_base import SkillResult

            return SkillResult.failure(f"Skill '{skill_name}' not found")

        try:
            result = await skill.execute(**kwargs)
            return result
        except Exception as e:
            logger.error(f"Error executing skill '{skill_name}': {e}")
            from skills.skill_base import SkillResult

            return SkillResult.failure(f"Skill execution error: {e}")

    def reload(self) -> dict[str, SkillBase]:
        """Reload all skills (useful for development)."""
        self._skills.clear()
        self._loaded = False
        return self.load_skills()


# Global registry instance
_registry: SkillRegistry | None = None


def get_registry(skills_dir: Path | None = None) -> SkillRegistry:
    """Get the global skill registry instance."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry(skills_dir)
    return _registry


def reset_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _registry
    _registry = None