import asyncio
import json
import unittest

import anthropic
import httpx2

from llm.anthropic_client import AnthropicClient, to_anthropic


def sse(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n" for name, data in events).encode()


def message_start():
    return ("message_start", {"message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
    }})


def end(stop_reason: str):
    return [("message_delta", {"delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 2}}),
            ("message_stop", {})]


def block(index: int, start: dict, *deltas: dict):
    return [("content_block_start", {"index": index, "content_block": start})] + \
        [("content_block_delta", {"index": index, "delta": d}) for d in deltas] + \
        [("content_block_stop", {"index": index})]


TEXT = sse(message_start(), *block(0, {"type": "text", "text": ""}, {"type": "text_delta", "text": "Hel"}, {"type": "text_delta", "text": "lo"}), *end("end_turn"))


class Server:
    """Records requests and answers with a canned response."""

    def __init__(self, body: bytes = TEXT, status: int = 200, error: Exception | None = None):
        self.body, self.status, self.error, self.requests = body, status, error, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        kind = "text/event-stream" if b"event:" in self.body else "application/json"
        return httpx2.Response(self.status, content=self.body, headers={"content-type": kind})

    def client(self) -> AnthropicClient:
        http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self))
        return AnthropicClient("sk-ant-test", base_url="http://anthropic.test", http_client=http)

    @property
    def body_sent(self) -> dict:
        return json.loads(self.requests[-1].content)


def run_stream(server: Server, model="claude-haiku-4-5", tools=None, messages=None) -> list[dict]:
    async def main():
        return [c async for c in server.client().chat_stream(messages or [{"role": "user", "content": "hi"}], model, tools)]

    return asyncio.run(main())


ASK = {"role": "user", "content": "q"}


class ToAnthropicTests(unittest.TestCase):
    def test_history_starts_at_a_user_turn(self):
        # A history window can open mid-exchange; the Messages API rejects a first turn that isn't the owner's.
        _, messages = to_anthropic([
            {"role": "tool", "tool_call_id": "gone", "name": "f", "content": "{}"},
            {"role": "assistant", "content": "earlier answer"},
            ASK,
        ])
        self.assertEqual(messages, [ASK])

    def test_system_messages_are_joined_and_removed(self):
        system, messages = to_anthropic([
            {"role": "system", "content": "a"}, {"role": "system", "content": "b"}, {"role": "user", "content": "hi"},
        ])
        self.assertEqual((system, messages), ("a\n\nb", [{"role": "user", "content": "hi"}]))

    def test_plain_assistant_message(self):
        _, messages = to_anthropic([ASK, {"role": "assistant", "content": "text"}])
        self.assertEqual(messages, [ASK, {"role": "assistant", "content": "text"}])

    def test_tool_calls_become_tool_use_blocks(self):
        call = {"id": "t1", "type": "function", "function": {"name": "f", "arguments": '{"x": 1}'}}
        _, messages = to_anthropic([ASK, {"role": "assistant", "content": "ok", "tool_calls": [call]}])
        self.assertEqual(messages[1]["content"], [
            {"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t1", "name": "f", "input": {"x": 1}},
        ])

    def test_replay_is_used_verbatim(self):
        replay = [{"type": "thinking", "thinking": "hm", "signature": "s"}, {"type": "text", "text": "x"}]
        call = {"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        _, messages = to_anthropic([ASK, {"role": "assistant", "content": "x", "tool_calls": [call], "replay": replay}])
        self.assertEqual(messages, [ASK, {"role": "assistant", "content": replay}])

    def test_consecutive_tool_results_share_one_user_message(self):
        _, messages = to_anthropic([
            ASK,
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": i, "type": "function", "function": {"name": "f", "arguments": "{}"}} for i in "ab"]},
            {"role": "tool", "tool_call_id": "a", "name": "f", "content": '{"success": true, "data": 1}'},
            {"role": "tool", "tool_call_id": "b", "name": "f", "content": '{"success": false, "error": "no"}'},
        ])
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[2]["role"], "user")
        blocks = messages[2]["content"]
        self.assertEqual([(b["type"], b["tool_use_id"], b.get("is_error", False)) for b in blocks],
                         [("tool_result", "a", False), ("tool_result", "b", True)])


class RequestShapeTests(unittest.TestCase):
    TOOLS = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}]

    def test_request_body(self):
        server = Server()
        run_stream(server, tools=self.TOOLS)
        body = server.body_sent
        self.assertEqual(body["max_tokens"], 64000)
        self.assertEqual(body["tools"], [{"name": "f", "description": "d", "input_schema": {"type": "object", "properties": {}}}])
        self.assertNotIn("eager_input_streaming", json.dumps(body))
        self.assertNotIn("fallbacks", body)
        self.assertNotIn("server-side-fallback", server.requests[-1].headers.get("anthropic-beta", ""))

    def test_fallback_models_ask_for_server_side_fallbacks(self):
        server = Server()
        run_stream(server, model="claude-opus-5-5")
        self.assertEqual(server.body_sent["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", server.requests[-1].headers["anthropic-beta"])


class StreamTests(unittest.TestCase):
    def test_text_deltas_become_content_chunks(self):
        self.assertEqual(run_stream(Server()), [{"content": "Hel"}, {"content": "lo"}])

    def test_tool_use_yields_calls_then_replay(self):
        body = sse(
            message_start(),
            *block(0, {"type": "thinking", "thinking": ""}, {"type": "thinking_delta", "thinking": "hm"}, {"type": "signature_delta", "signature": "sig"}),
            *block(1, {"type": "tool_use", "id": "t1", "name": "f", "input": {}}, {"type": "input_json_delta", "partial_json": '{"a":'}, {"type": "input_json_delta", "partial_json": "1}"}),
            *block(2, {"type": "tool_use", "id": "t2", "name": "g", "input": {}}, {"type": "input_json_delta", "partial_json": '{"b": 2}'}),
            *end("tool_use"),
        )
        chunks = run_stream(Server(body))
        self.assertEqual(chunks[0], {"tool_calls": [
            {"index": 0, "id": "t1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}},
            {"index": 1, "id": "t2", "type": "function", "function": {"name": "g", "arguments": '{"b": 2}'}},
        ]})
        self.assertEqual(len(chunks), 2)
        replay = chunks[1]["replay"]
        self.assertEqual([b["type"] for b in replay], ["thinking", "tool_use", "tool_use"])
        self.assertEqual(replay[0]["thinking"], "hm")
        self.assertEqual(replay[1]["input"], {"a": 1})

    def test_refusal_is_an_error_without_tools(self):
        body = sse(message_start(), *block(0, {"type": "text", "text": ""}, {"type": "text_delta", "text": "no"}), *end("refusal"))
        chunks = run_stream(Server(body))
        self.assertEqual(chunks[-1], {"error": True, "message": "Claude declined this request."})
        self.assertFalse(any("tool_calls" in c for c in chunks))

    def test_max_tokens_is_an_error(self):
        chunks = run_stream(Server(sse(message_start(), *end("max_tokens"))))
        self.assertEqual(chunks, [{"error": True, "message": "Claude's reply hit the length limit before finishing."}])

    def test_http_errors(self):
        def err(kind, message):
            return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()

        auth = run_stream(Server(err("authentication_error", "bad"), 401))
        self.assertIn("API key", auth[0]["message"])
        rate = run_stream(Server(err("rate_limit_error", "slow"), 429))
        self.assertIn("rate limiting", rate[0]["message"])
        other = run_stream(Server(err("invalid_request_error", "boom"), 400))
        self.assertTrue(other[0]["message"].startswith("Anthropic error 400:"))
        self.assertIn("boom", other[0]["message"])

    def test_connection_failure(self):
        chunks = run_stream(Server(error=httpx2.ConnectError("down")))
        self.assertTrue(chunks[0]["message"].startswith("Could not reach Anthropic"))

    def test_transport_drop_mid_stream_is_an_error_chunk(self):
        class Dropping(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield sse(message_start())
                raise httpx2.ReadError("dropped")

        server = Server()
        server.__class__ = type("DropServer", (Server,), {
            "__call__": lambda self, request: httpx2.Response(200, stream=Dropping(), headers={"content-type": "text/event-stream"})})
        chunks = run_stream(server)
        self.assertEqual([c.get("error") for c in chunks], [True])

    def test_malformed_stored_arguments_are_an_error_chunk(self):
        call = {"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{oops"}}
        chunks = run_stream(Server(), messages=[{"role": "assistant", "content": "", "tool_calls": [call]}])
        self.assertEqual([c.get("error") for c in chunks], [True])

    def test_chat_collects_the_stream(self):
        result = asyncio.run(Server().client().chat([{"role": "user", "content": "hi"}], "claude-haiku-4-5"))
        self.assertEqual(result["choices"][0]["message"]["content"], "Hello")


class ListModelsTests(unittest.TestCase):
    def test_maps_id_and_display_name(self):
        body = json.dumps({"data": [{"type": "model", "id": "claude-x", "display_name": "Claude X", "created_at": "2026-01-01T00:00:00Z"}],
                           "has_more": False, "first_id": "claude-x", "last_id": "claude-x"}).encode()
        models = asyncio.run(Server(body).client().list_models())
        self.assertEqual(models, [{"id": "claude-x", "name": "Claude X"}])

    def test_rejected_key_raises(self):
        client = Server(b"{}", 401).client()
        with self.assertRaises(anthropic.AuthenticationError):
            asyncio.run(client.list_models())


if __name__ == "__main__":
    unittest.main()
