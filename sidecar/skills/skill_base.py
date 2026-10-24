"""Abstract base class for Tesseract skills."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal


@dataclass
class SkillResult:
    """Result of a skill execution."""
    success: bool
    data: Any = None
    error: str | None = None

    @classmethod
    def success(cls, data: Any = None) -> "SkillResult":
        return cls(success=True, data=data)

    @classmethod
    def failure(cls, error: str) -> "SkillResult":
        return cls(success=False, error=error)


class SkillBase(ABC):
    """Abstract base class for all Tesseract skills.

    Each skill must implement:
    - name: unique identifier for the skill
    - description: human-readable description for the LLM
    - parameters: JSON schema describing the skill's parameters
    - execute: the actual implementation

    Skills that only read declare kind = "read"; anything else asks the owner first.
    """

    kind: ClassVar[Literal["read", "change"]] = "change"
    irreversible: ClassVar[bool] = False
    approval_title: ClassVar[str] = ""  # imperative label for the approval card, e.g. "Send an email"

    def approval_summary(self, arguments: dict) -> str:
        """One line telling the owner what this call would do."""
        return self.description

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique skill name (e.g., 'gmail.read_inbox', 'filesystem.read_file')."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the LLM to understand when to use this skill."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema describing the skill's parameters.

        Should follow OpenAI function calling schema format:
        {
            "type": "object",
            "properties": {
                "param_name": {"type": "string", "description": "..."}
            },
            "required": ["param_name"]
        }
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs: Any) -> SkillResult:
        """Execute the skill with the given parameters.

        Args:
            **kwargs: Parameters matching the schema defined in `parameters`

        Returns:
            SkillResult with success status, data, or error message
        """
        pass

    def to_tool_definition(self) -> dict[str, Any]:
        """Convert skill to OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class BlockingSkill(SkillBase):
    """A skill built on a blocking client library: `run` happens off the event loop so other requests keep flowing."""

    @abstractmethod
    def run(self, **kwargs: Any) -> SkillResult:
        pass

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await asyncio.to_thread(self.run, **kwargs)
