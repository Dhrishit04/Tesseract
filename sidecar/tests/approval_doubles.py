"""Test doubles shared by the approval tests: a change skill, a mocked model and a private HOME."""

import json
import tempfile
from typing import Any
from unittest.mock import patch

import httpx

from llm.openrouter import OpenRouterClient
from skills.registry import SkillRegistry
from skills.skill_base import SkillBase, SkillResult
from tests.test_chat_endpoints import EchoSkill, delta, sse


class WriteSkill(SkillBase):
    """A change tool that records every run."""

    approval_title = "Write a test file"
    calls: list[dict] = []
    name = "test.write"
    description = "Write a test file."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> SkillResult:
        WriteSkill.calls.append(kwargs)
        return SkillResult.success({"written": kwargs.get("path")})


def make_registry() -> SkillRegistry:
    WriteSkill.calls = []
    EchoSkill.calls = []
    registry = SkillRegistry()
    registry._skills = {"test.write": WriteSkill(), "test.echo": EchoSkill()}
    registry._loaded = True
    return registry


def calls_response(*calls: tuple[str, str, str]) -> httpx.Response:
    """One streamed model turn making the given (id, tool name, raw arguments) calls."""
    fragments = [
        {"index": index, "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        for index, (call_id, name, arguments) in enumerate(calls)
    ]
    return httpx.Response(200, content=sse(delta(tool_calls=fragments)))


def text_response(text: str) -> httpx.Response:
    return httpx.Response(200, content=sse(delta(content=text)))


class FakeModel:
    """Answers each request with the next queued response and keeps the request bodies."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.client = OpenRouterClient(api_key="test-key", transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.responses.pop(0)

    def tool_results(self, request_index: int) -> list[dict]:
        """The tool messages the model was sent in a request, decoded."""
        return [json.loads(m["content"]) for m in self.requests[request_index]["messages"] if m["role"] == "tool"]


class PrivateHome:
    """Mixin: point ~ at a temporary folder so the trust list and chats never touch the real ones."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.home_patch = patch("os.path.expanduser", return_value=self.home.name)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.home.cleanup()
