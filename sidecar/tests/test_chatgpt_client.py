"""ChatGPT models over the Responses API: request shape, translation, stream mapping and errors."""

import asyncio
import json
import unittest

import httpx

from auth.chatgpt import ChatGPTAuthError
from llm.chatgpt_client import TOOL_NAMESPACE, ChatGPTClient, to_responses

TOOLS = [{"type": "function", "function": {"name": "files__read", "description": "Read", "parameters": {"type": "object"}}}]
UNSUPPORTED = ("max_output_tokens", "temperature", "top_p", "previous_response_id", "metadata", "user")


class FakeAccount:
    def __init__(self, error=None):
        self.error = error

    async def access_token(self):
        if self.error:
            raise self.error
        return "at"


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def call_item(n, **extra):
    return {"type": "function_call", "id": f"fc_{n}", "status": "completed", "call_id": f"call_{n}",
            "name": f"files__read{n}", "arguments": "{}", **extra}


def run_stream(body=None, status=200, tools=None, account=None, messages=None):
    seen = []

    def handler(request):
        seen.append(request)
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body or "", headers={"content-type": "text/event-stream"})

    client = ChatGPTClient(account or FakeAccount(), base_url="http://x/v1", transport=httpx.MockTransport(handler))

    async def go():
        return [c async for c in client.chat_stream(messages or [{"role": "user", "content": "hi"}], "gpt-x", tools)]

    return asyncio.run(go()), seen


class RequestTests(unittest.TestCase):
    def test_request_shape(self):
        messages = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]
        _, seen = run_stream(sse({"type": "response.completed"}), tools=TOOLS, messages=messages)
        request = seen[0]
        self.assertEqual(request.url.path, "/v1/responses")
        self.assertEqual(request.headers["authorization"], "Bearer at")
        body = json.loads(request.content)
        self.assertIs(body["store"], False)
        self.assertIs(body["stream"], True)
        self.assertEqual(body["instructions"], "Be brief.")
        self.assertEqual(body["input"], [{"role": "user", "content": "hi"}])
        for field in UNSUPPORTED:
            self.assertNotIn(field, body)
        namespace = body["tools"][0]
        self.assertEqual((namespace["type"], namespace["name"]), ("namespace", TOOL_NAMESPACE))
        self.assertTrue(namespace["description"])
        self.assertEqual(namespace["tools"], [{"type": "function", "name": "files__read", "description": "Read",
                                               "parameters": {"type": "object"}, "strict": False}])
        self.assertEqual(len(body["tools"]), 1)

    def test_no_tools_field_without_tools(self):
        _, seen = run_stream(sse({"type": "response.completed"}))
        self.assertNotIn("tools", json.loads(seen[0].content))


class TranslationTests(unittest.TestCase):
    def test_messages(self):
        calls = [{"id": "c1", "type": "function", "function": {"name": "a__b", "arguments": '{"x": 1}'}}]
        instructions, items = to_responses([
            {"role": "system", "content": "S"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
            {"role": "assistant", "content": "", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ])
        self.assertEqual(instructions, "S")
        self.assertEqual(items, [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
            {"type": "function_call", "call_id": "c1", "name": "a__b", "arguments": '{"x": 1}'},
            {"type": "function_call_output", "call_id": "c1", "output": '{"ok": true}'},
        ])

    def test_replay_is_used_verbatim(self):
        replay = [{"type": "function_call", "call_id": "c1", "name": "n", "arguments": "{}", "namespace": "tesseract"}]
        _, items = to_responses([{"role": "assistant", "content": "", "tool_calls": [{"id": "x"}], "replay": replay}])
        self.assertEqual(items, replay)


class StreamTests(unittest.TestCase):
    def test_text_deltas(self):
        chunks, _ = run_stream(sse({"type": "response.output_text.delta", "delta": "Hi "},
                                   {"type": "response.output_text.delta", "delta": "there"},
                                   {"type": "response.completed"}))
        self.assertEqual(chunks, [{"content": "Hi "}, {"content": "there"}])

    def test_function_calls_and_replay(self):
        chunks, _ = run_stream(sse({"type": "response.output_item.done", "item": call_item(1, namespace="tesseract")},
                                   {"type": "response.output_item.done", "item": call_item(2)},
                                   {"type": "response.completed"}))
        self.assertEqual(chunks[0], {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": "files__read1", "arguments": "{}"}},
            {"index": 1, "id": "call_2", "type": "function", "function": {"name": "files__read2", "arguments": "{}"}},
        ]})
        replay = chunks[1]["replay"]
        self.assertEqual(len(replay), 2)
        self.assertEqual(replay[0]["namespace"], "tesseract")
        for item in replay:
            self.assertNotIn("id", item)
            self.assertNotIn("status", item)
            self.assertEqual(item["type"], "function_call")

    def test_stream_without_completed_is_an_error_and_drops_calls(self):
        chunks, _ = run_stream(sse({"type": "response.output_item.done", "item": call_item(1)}))
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["error"])
        self.assertIn("interrupted", chunks[0]["message"])


class ErrorTests(unittest.TestCase):
    def message(self, *args, **kwargs):
        chunks, _ = run_stream(*args, **kwargs)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]["error"])
        return chunks[0]["message"]

    def test_failed_usage_limit(self):
        failed = {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "m"}}}
        message = self.message(sse(failed))
        self.assertIn("usage limit", message)
        self.assertIn("https://chatgpt.com/settings/usage", message)

    def test_error_event(self):
        message = self.message(sse({"type": "error", "code": "weird", "message": "boom"}))
        self.assertEqual(message, "ChatGPT error weird: boom")

    def test_unsupported_capability_names_param(self):
        body = {"error": {"code": "subscription_sharing_unsupported_capability", "message": "m", "param": "temperature"}}
        self.assertIn("temperature", self.message(body, status=400))

    def test_401(self):
        self.assertIn("sign in again", self.message({"error": {"message": "no"}}, status=401))

    def test_503_detail(self):
        self.assertIn("temporarily unavailable", self.message({"detail": "x"}, status=503))

    def test_other_status(self):
        self.assertEqual(self.message({"detail": "teapot"}, status=418), "ChatGPT error 418: teapot")

    def test_incomplete(self):
        event = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}
        self.assertEqual(self.message(sse(event)), "ChatGPT stopped before finishing (max_output_tokens).")

    def test_auth_error_message_is_yielded(self):
        self.assertEqual(self.message(account=FakeAccount(ChatGPTAuthError("Sign in again."))), "Sign in again.")


class ListModelsTests(unittest.TestCase):
    def test_keeps_listed_models_in_order(self):
        data = {"models": [
            {"slug": "b", "display_name": "B", "visibility": "list"},
            {"slug": "h", "display_name": "H", "visibility": "hide"},
            {"slug": "a", "display_name": "A", "visibility": "list"},
        ]}
        client = ChatGPTClient(FakeAccount(), base_url="http://x/v1", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)))
        self.assertEqual(asyncio.run(client.list_models()), [{"id": "b", "name": "B"}, {"id": "a", "name": "A"}])

    def test_http_failure_raises(self):
        client = ChatGPTClient(FakeAccount(), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client.list_models())

    def test_dead_session_raises(self):
        client = ChatGPTClient(FakeAccount(ChatGPTAuthError("x")), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        with self.assertRaises(ChatGPTAuthError):
            asyncio.run(client.list_models())


if __name__ == "__main__":
    unittest.main()
