"""Approval cards over HTTP: answering them, the trust list, and what a chat saves."""

import asyncio
import unittest
from unittest.mock import patch

import httpx

import chat_store
import server
from permissions import get_broker, reset_broker, trust
from tests.approval_doubles import FakeModel, PrivateHome, WriteSkill, calls_response, make_registry, text_response
from tests.test_approval_reply import answered
from tests.test_chat_endpoints import delta, read_events, sse

WRITE = ("call_1", "test__write", '{"path": "a.txt"}')


class ApprovalEndpointTests(PrivateHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        reset_broker()
        self.model = FakeModel()
        self.patches = [
            patch.object(server, "client_for", return_value=(self.model.client, "m")),
            patch.object(server, "get_skill_registry", return_value=make_registry()),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in self.patches:
            active.stop()
        if chat_store._store is not None:
            chat_store._store.close()
            chat_store._store = None
        reset_broker()
        super().tearDown()

    def run_http(self, scenario):
        async def main():
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
                return await scenario(http)

        return asyncio.run(main())

    async def new_chat(self, http) -> str:
        return (await http.post("/chats", json={"model": "m"})).json()["id"]

    async def pending(self, chat_id: str):
        for _ in range(200):
            request = next((r for r, _ in get_broker()._pending.values() if r.chat_id == chat_id), None)
            if request:
                return request
            await asyncio.sleep(0.01)
        raise AssertionError("no approval card appeared")

    async def ask(self, http, chat_id: str, answer, message: str = "save it"):
        """Send a message, wait for its approval card, answer it, and return (stream events, answer result)."""
        stream = asyncio.create_task(http.post("/chat/stream", json={"message": message, "chat_id": chat_id}))
        result = await answer(await self.pending(chat_id))
        return read_events(await stream), result

    def queue_write_turn(self):
        self.model.responses += [calls_response(WRITE), text_response("done")]

    def test_allow_button_runs_the_tool_and_stream_finishes(self):
        self.queue_write_turn()

        async def scenario(http):
            chat_id = await self.new_chat(http)
            return await self.ask(http, chat_id, lambda card: http.post(f"/chat/approvals/{card.id}", json={"decision": "allow"}))

        events, answer = self.run_http(scenario)
        self.assertEqual(answer.json(), {"success": True})
        self.assertEqual(WriteSkill.calls, [{"path": "a.txt"}])
        self.assertIn({"content": "done"}, events)
        self.assertEqual(events[-1], "[DONE]")

    def test_always_adds_to_trusted_list_endpoint(self):
        self.queue_write_turn()

        async def scenario(http):
            chat_id = await self.new_chat(http)
            await self.ask(http, chat_id, lambda card: http.post(f"/chat/approvals/{card.id}", json={"decision": "always"}))
            return (await http.get("/permissions/trusted")).json()

        self.assertEqual(self.run_http(scenario), {"trusted": [{"tool": "test.write", "title": "Write a test file"}]})

    def test_trusted_tool_that_no_longer_exists_is_listed_by_name(self):
        trust("old.tool")

        async def scenario(http):
            return (await http.get("/permissions/trusted")).json()

        self.assertEqual(self.run_http(scenario), {"trusted": [{"tool": "old.tool", "title": "old.tool"}]})

    def test_revoke_removes_trust(self):
        trust("test.write")

        async def scenario(http):
            deleted = await http.delete("/permissions/trusted/test.write")
            return deleted.status_code, (await http.get("/permissions/trusted")).json()

        self.assertEqual(self.run_http(scenario), (200, {"trusted": []}))

    def test_revoke_unknown_is_404(self):
        async def scenario(http):
            return (await http.delete("/permissions/trusted/nothing.here")).status_code

        self.assertEqual(self.run_http(scenario), 404)

    def test_resolving_twice_is_404_and_tool_runs_once(self):
        self.queue_write_turn()

        async def answer_twice(http, card):
            first = await http.post(f"/chat/approvals/{card.id}", json={"decision": "allow"})
            second = await http.post(f"/chat/approvals/{card.id}", json={"decision": "deny"})
            return first.status_code, second.status_code, second.json()

        async def scenario(http):
            chat_id = await self.new_chat(http)
            return await self.ask(http, chat_id, lambda card: answer_twice(http, card))

        _, (first, second, body) = self.run_http(scenario)
        self.assertEqual((first, second), (200, 404))
        self.assertEqual(body["detail"], "This request has already ended.")
        self.assertEqual(len(WriteSkill.calls), 1)

    def test_unknown_decision_is_422(self):
        async def scenario(http):
            return (await http.post("/chat/approvals/abc", json={"decision": "maybe"})).status_code

        self.assertEqual(self.run_http(scenario), 422)

    def test_declined_tool_is_saved_with_the_chat(self):
        self.queue_write_turn()

        async def scenario(http):
            chat_id = await self.new_chat(http)
            await self.ask(http, chat_id, lambda card: http.post(f"/chat/approvals/{card.id}", json={"decision": "deny"}))
            return (await http.get(f"/chats/{chat_id}")).json()["messages"]

        messages = self.run_http(scenario)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["tools"], [{"name": "test.write", "status": "declined", "error": None}])
        self.assertEqual(WriteSkill.calls, [])

    def reply_scenario(self, text: str):
        async def scenario(http):
            chat_id = await self.new_chat(http)
            events, reply = await self.ask(
                http, chat_id, lambda card: http.post(f"/chat/approvals/{card.id}/reply", json={"text": text})
            )
            return events, reply.json(), (await http.get(f"/chats/{chat_id}")).json()["messages"]

        return self.run_http(scenario)

    def queue_reply_turn(self, verdict: str):
        """A model turn that says a line, calls the write tool, gets classified, then finishes."""
        say_then_call = sse(delta(content="Saving it now."), delta(tool_calls=[
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": "test__write", "arguments": '{"path": "a.txt"}'}}
        ]))
        self.model.responses += [httpx.Response(200, content=say_then_call), answered(verdict), text_response("done")]

    def test_typed_reply_allows_and_is_saved_in_order(self):
        self.queue_reply_turn("allow")

        events, reply, messages = self.reply_scenario("yes go ahead")

        self.assertEqual(reply, {"decision": "allow"})
        self.assertEqual(WriteSkill.calls, [{"path": "a.txt"}])
        resolved = next(e["approval_resolved"] for e in events if isinstance(e, dict) and "approval_resolved" in e)
        self.assertEqual((resolved["decision"], resolved["via"], resolved["reply"]), ("allow", "reply", "yes go ahead"))
        # Stored as the owner saw it: the question, their reply, then the action and the answer.
        self.assertEqual([(m["role"], m["content"]) for m in messages],
                         [("user", "save it"), ("assistant", "Saving it now."), ("user", "yes go ahead"), ("assistant", "done")])
        self.assertEqual(messages[3]["tools"], [{"name": "test.write", "status": "done", "error": None}])

    def test_typed_other_reply_denies_with_message(self):
        self.queue_reply_turn("other")

        _, reply, messages = self.reply_scenario("check the other folder first")

        self.assertEqual(reply, {"decision": "other"})
        self.assertEqual(WriteSkill.calls, [])
        self.assertEqual(messages[3]["tools"], [{"name": "test.write", "status": "declined", "error": None}])
        sent = self.model.tool_results(2)[0]["error"]
        self.assertIn("check the other folder first", sent)

    def test_reply_is_classified_through_the_turns_own_provider(self):
        self.queue_reply_turn("allow")

        async def scenario(http):
            chat_id = (await http.post("/chats", json={"model": "anthropic:c"})).json()["id"]
            await self.ask(http, chat_id, lambda card: http.post(f"/chat/approvals/{card.id}/reply", json={"text": "yes"}))

        self.run_http(scenario)

        self.assertEqual(server.client_for.call_args_list[-1].args, ("anthropic:c",))

    def test_reply_to_unknown_approval_is_404(self):
        async def scenario(http):
            return (await http.post("/chat/approvals/nope/reply", json={"text": "yes"})).status_code

        self.assertEqual(self.run_http(scenario), 404)


if __name__ == "__main__":
    unittest.main()
