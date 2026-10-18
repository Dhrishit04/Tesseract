"""Integration tests for the chat endpoints against a mocked OpenRouter API."""

import json
import re
import unittest
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import server
from llm.openrouter import OpenRouterClient
from skills.registry import SkillRegistry
from skills.skill_base import SkillBase, SkillResult


class EchoSkill(SkillBase):
    """Minimal skill used to observe tool execution."""

    calls: list[dict] = []

    @property
    def name(self) -> str:
        return "test.echo"

    @property
    def description(self) -> str:
        return "Echo the provided text."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        EchoSkill.calls.append(kwargs)
        return SkillResult.success({"echo": kwargs["text"]})


def sse(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def delta(**fields: Any) -> dict:
    return {"choices": [{"delta": fields}]}


def read_events(response) -> list[Any]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            events.append(payload if payload == "[DONE]" else json.loads(payload))
    return events


class ChatEndpointTests(unittest.TestCase):
    def setUp(self):
        self.requests: list[dict] = []
        self.responses: list[httpx.Response] = []
        EchoSkill.calls = []

        registry = SkillRegistry()
        registry._skills = {"test.echo": EchoSkill()}
        registry._loaded = True

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            return self.responses.pop(0)

        client = OpenRouterClient(api_key="test-key", transport=httpx.MockTransport(handler))
        self.patches = [
            patch.object(server, "get_client", return_value=client),
            patch.object(server, "get_skill_registry", return_value=registry),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        for active_patch in self.patches:
            active_patch.stop()

    def test_stream_relays_content_deltas_to_frontend(self):
        self.responses.append(httpx.Response(200, content=sse(delta(content="Hel"), delta(content="lo"))))

        response = self.client.post("/chat/stream", json={"message": "hi", "model": "m"})

        events = read_events(response)
        self.assertEqual([e["content"] for e in events if isinstance(e, dict)], ["Hel", "lo"])
        self.assertEqual(events[-1], "[DONE]")

    def test_stream_surfaces_upstream_error(self):
        self.responses.append(httpx.Response(401, content=b'{"error":"bad key"}'))

        events = read_events(self.client.post("/chat/stream", json={"message": "hi"}))

        errors = [e for e in events if isinstance(e, dict) and e.get("error")]
        self.assertEqual(len(errors), 1)
        self.assertIn("401", errors[0]["message"])

    def test_tool_names_sent_upstream_are_api_safe(self):
        self.responses.append(httpx.Response(200, content=sse(delta(content="ok"))))

        self.client.post("/chat/stream", json={"message": "hi"})

        names = [tool["function"]["name"] for tool in self.requests[0]["tools"]]
        self.assertEqual(names, ["test__echo"])
        for name in names:
            self.assertRegex(name, re.compile(r"^[a-zA-Z0-9_-]{1,64}$"))

    def test_stream_executes_tool_call_then_continues(self):
        tool_call = {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "test__echo", "arguments": '{"text": "pi'},
        }
        tool_call_tail = {"index": 0, "function": {"arguments": 'ng"}'}}
        self.responses.append(httpx.Response(200, content=sse(delta(tool_calls=[tool_call]), delta(tool_calls=[tool_call_tail]))))
        self.responses.append(httpx.Response(200, content=sse(delta(content="done"))))

        events = read_events(self.client.post("/chat/stream", json={"message": "echo ping"}))

        self.assertEqual(EchoSkill.calls, [{"text": "ping"}])
        tool_events = [e["tool"] for e in events if isinstance(e, dict) and "tool" in e]
        self.assertEqual([(t["name"], t["status"]) for t in tool_events], [("test.echo", "running"), ("test.echo", "done")])
        self.assertEqual([e["content"] for e in events if isinstance(e, dict) and "content" in e], ["done"])
        followup = self.requests[1]["messages"]
        self.assertEqual(followup[-1]["role"], "tool")
        self.assertEqual(followup[-1]["tool_call_id"], "call_1")
        self.assertEqual(json.loads(followup[-1]["content"])["data"], {"echo": "ping"})

    def test_non_streaming_chat_runs_tools(self):
        first = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_9", "type": "function", "function": {"name": "test__echo", "arguments": '{"text": "x"}'}}
        ]}}]}
        second = {"choices": [{"message": {"role": "assistant", "content": "finished"}}]}
        self.responses.extend([httpx.Response(200, json=first), httpx.Response(200, json=second)])

        response = self.client.post("/chat", json={"message": "go"})

        self.assertEqual(response.json(), {"response": "finished"})
        self.assertEqual(EchoSkill.calls, [{"text": "x"}])


if __name__ == "__main__":
    unittest.main()
