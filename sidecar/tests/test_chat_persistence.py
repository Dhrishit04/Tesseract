"""Saved chats over HTTP: CRUD routes and /chat/stream persisting both turns."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import chat_store
import server
from llm.openrouter import OpenRouterClient
from skills.registry import SkillRegistry
from tests.test_chat_endpoints import EchoSkill, delta, read_events, sse


class ChatPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.requests: list[dict] = []
        self.replies: list = []
        self.on_request = None
        EchoSkill.calls = []

        registry = SkillRegistry()
        registry._skills = {"test.echo": EchoSkill()}
        registry._loaded = True

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            if self.on_request:
                self.on_request()
            return self.replies.pop(0)

        client = OpenRouterClient(api_key="k", transport=httpx.MockTransport(handler))
        self.patches = [
            patch("os.path.expanduser", return_value=self.home.name),
            patch.object(server, "get_client", return_value=client),
            patch.object(server, "get_skill_registry", return_value=registry),
        ]
        for active in self.patches:
            active.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        for active in self.patches:
            active.stop()
        if chat_store._store is not None:
            chat_store._store.close()
            chat_store._store = None
        self.home.cleanup()

    def new_chat(self) -> str:
        return self.client.post("/chats", json={"model": "mock/fast"}).json()["id"]

    def stream(self, chat_id: str, message: str) -> list:
        return read_events(self.client.post("/chat/stream", json={"message": message, "chat_id": chat_id}))

    def test_crud_round_trip(self):
        chat_id = self.new_chat()
        self.assertIn(chat_id, [chat["id"] for chat in self.client.get("/chats").json()["chats"]])

        self.client.patch(f"/chats/{chat_id}", json={"title": "Weekly plan"})
        self.assertEqual(self.client.get(f"/chats/{chat_id}").json()["title"], "Weekly plan")

        self.assertEqual(self.client.delete(f"/chats/{chat_id}").json(), {"success": True})
        self.assertEqual(self.client.get(f"/chats/{chat_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/chats/{chat_id}").status_code, 404)

    def test_stream_persists_both_turns_and_titles_chat(self):
        chat_id = self.new_chat()
        self.replies.append(httpx.Response(200, content=sse(delta(content="Sure"))))

        self.stream(chat_id, "Plan my week")

        chat = self.client.get(f"/chats/{chat_id}").json()
        self.assertEqual([(m["role"], m["content"]) for m in chat["messages"]], [("user", "Plan my week"), ("assistant", "Sure")])
        self.assertEqual(chat["title"], "Plan my week")

    def test_second_turn_sends_stored_history_upstream(self):
        chat_id = self.new_chat()
        self.replies.extend([
            httpx.Response(200, content=sse(delta(content="First answer"))),
            httpx.Response(200, content=sse(delta(content="Second answer"))),
        ])

        self.stream(chat_id, "first question")
        self.stream(chat_id, "second question")

        sent = [(m["role"], m["content"]) for m in self.requests[1]["messages"] if m["role"] in ("user", "assistant")]
        self.assertEqual(sent[:3], [("user", "first question"), ("assistant", "First answer"), ("user", "second question")])

    def test_tool_events_are_stored_with_the_answer(self):
        chat_id = self.new_chat()
        call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "test__echo", "arguments": '{"text": "x"}'}}
        self.replies.extend([
            httpx.Response(200, content=sse(delta(tool_calls=[call]))),
            httpx.Response(200, content=sse(delta(content="done"))),
        ])

        self.stream(chat_id, "echo x")

        answer = self.client.get(f"/chats/{chat_id}").json()["messages"][-1]
        self.assertEqual(answer["tools"], [{"name": "test.echo", "status": "done", "error": None}])

    def test_unknown_chat_id_reports_error(self):
        events = self.stream("nope", "hello")

        errors = [e for e in events if isinstance(e, dict) and e.get("error")]
        self.assertEqual([e.get("code") for e in errors], ["chat_not_found"])
        self.assertEqual(self.requests, [])

    def test_deleted_mid_stream_is_not_resurrected(self):
        chat_id = self.new_chat()
        self.replies.append(httpx.Response(200, content=sse(delta(content="late"))))
        self.on_request = lambda: chat_store.get_chat_store().delete_chat(chat_id)

        self.stream(chat_id, "hello")

        self.assertEqual(self.client.get(f"/chats/{chat_id}").status_code, 404)
        with closing(sqlite3.connect(Path(self.home.name) / ".tesseract" / "tesseract.db")) as raw:
            self.assertEqual(raw.execute("SELECT count(*) FROM chats").fetchone()[0], 0)
            self.assertEqual(raw.execute("SELECT count(*) FROM messages").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
