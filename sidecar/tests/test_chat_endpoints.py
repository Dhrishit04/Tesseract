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


class ScreenshotSkill(EchoSkill):
    @property
    def name(self) -> str:
        return "test.snap"

    async def execute(self, **kwargs: Any) -> SkillResult:
        return SkillResult.success({"image_base64": "iVBORw0KGgo=", "width": 1})


class ToolPreviewTests(unittest.TestCase):
    def test_screenshot_tool_event_carries_image_preview(self):
        registry = SkillRegistry()
        registry._skills = {"test.snap": ScreenshotSkill()}
        registry._loaded = True
        call = {"index": 0, "id": "c", "type": "function", "function": {"name": "test__snap", "arguments": "{}"}}
        replies = [
            httpx.Response(200, content=sse(delta(tool_calls=[call]))),
            httpx.Response(200, content=sse(delta(content="here"))),
        ]
        client = OpenRouterClient(api_key="k", transport=httpx.MockTransport(lambda _r: replies.pop(0)))
        with patch.object(server, "get_client", return_value=client), \
                patch.object(server, "get_skill_registry", return_value=registry):
            events = read_events(TestClient(server.app).post("/chat/stream", json={"message": "snap"}))

        done = [e["tool"] for e in events if isinstance(e, dict) and e.get("tool", {}).get("status") == "done"]
        self.assertEqual(done[0]["preview"], "data:image/png;base64,iVBORw0KGgo=")

    def test_screenshot_bytes_are_not_sent_to_the_model(self):
        registry = SkillRegistry()
        registry._skills = {"test.snap": ScreenshotSkill()}
        registry._loaded = True
        sent: list[dict] = []
        call = {"index": 0, "id": "c", "type": "function", "function": {"name": "test__snap", "arguments": "{}"}}
        replies = [
            httpx.Response(200, content=sse(delta(tool_calls=[call]))),
            httpx.Response(200, content=sse(delta(content="ok"))),
        ]

        def handler(request):
            sent.append(json.loads(request.content))
            return replies.pop(0)

        client = OpenRouterClient(api_key="k", transport=httpx.MockTransport(handler))
        with patch.object(server, "get_client", return_value=client),                 patch.object(server, "get_skill_registry", return_value=registry):
            read_events(TestClient(server.app).post("/chat/stream", json={"message": "snap"}))

        tool_message = sent[1]["messages"][-1]
        self.assertNotIn("iVBORw0KGgo=", tool_message["content"])
        self.assertEqual(json.loads(tool_message["content"])["data"]["width"], 1)


class ClientConfigurationTests(unittest.TestCase):
    def test_openrouter_base_url_can_be_overridden(self):
        with patch.dict("os.environ", {"OPENROUTER_BASE_URL": "http://127.0.0.1:9999/v1"}), \
                patch.object(server, "_client", None), \
                patch.object(server, "load_settings", return_value={"openrouter_api_key": "k"}):
            self.assertEqual(server.get_client().base_url, "http://127.0.0.1:9999/v1")


class ReviewFixTests(unittest.TestCase):
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

        client = OpenRouterClient(api_key="k", transport=httpx.MockTransport(handler))
        self.patches = [
            patch.object(server, "get_client", return_value=client),
            patch.object(server, "get_skill_registry", return_value=registry),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in self.patches:
            active.stop()

    def test_malformed_tool_arguments_are_reported_not_run(self):
        call = {"index": 0, "id": "c", "type": "function", "function": {"name": "test__echo", "arguments": '{"text": "pi'}}
        self.responses.extend([
            httpx.Response(200, content=sse(delta(tool_calls=[call]))),
            httpx.Response(200, content=sse(delta(content="sorry"))),
        ])

        events = read_events(TestClient(server.app).post("/chat/stream", json={"message": "echo"}))

        self.assertEqual(EchoSkill.calls, [])
        done = [e["tool"] for e in events if isinstance(e, dict) and e.get("tool", {}).get("status") == "error"]
        self.assertEqual(len(done), 1)
        result = json.loads(self.requests[1]["messages"][-1]["content"])
        self.assertFalse(result["success"])
        self.assertIn("not valid JSON", result["error"])

    def test_profile_is_given_to_the_model(self):
        self.responses.append(httpx.Response(200, content=sse(delta(content="hey boss"))))
        profile = {"profile": {"name": "Dhrishit", "address_as": "boss", "about": "Final-year student"}}

        with patch.object(server, "load_settings", return_value=profile):
            read_events(TestClient(server.app).post("/chat/stream", json={"message": "hi"}))

        system = self.requests[0]["messages"][0]
        self.assertEqual(system["role"], "system")
        for expected in ("Dhrishit", "boss", "Final-year student"):
            self.assertIn(expected, system["content"])

    def test_no_profile_means_no_system_message(self):
        self.responses.append(httpx.Response(200, content=sse(delta(content="hi"))))

        with patch.object(server, "load_settings", return_value={}):
            read_events(TestClient(server.app).post("/chat/stream", json={"message": "hi"}))

        self.assertEqual(self.requests[0]["messages"][0]["role"], "user")


class StartupTests(unittest.TestCase):
    def test_slow_google_refresh_does_not_delay_startup(self):
        import time as clock

        class SlowOAuth:
            def load_settings(self, _settings):
                pass

            def load_credentials(self):
                clock.sleep(3)

        with patch.object(server, "get_oauth_manager", return_value=SlowOAuth()):
            started = clock.monotonic()
            with TestClient(server.app) as client:
                elapsed = clock.monotonic() - started
                self.assertEqual(client.get("/health").status_code, 200)

        self.assertLess(elapsed, 1.5)
