"""The chat loop asks before running a change tool and tells the model what the owner decided."""

import asyncio
import unittest
from unittest.mock import patch

import httpx

import permissions
from llm.chat_loop import DECLINED, DECLINED_WITH_REPLY, NEEDS_CHAT, TIMED_OUT, complete_chat, stream_chat
from permissions import ApprovalBroker, Decision, load_trusted, trust
from tests.approval_doubles import FakeModel, PrivateHome, WriteSkill, calls_response, make_registry, text_response
from tests.test_chat_endpoints import EchoSkill

WRITE = ("call_1", "test__write", '{"path": "a.txt"}')


async def drive(model: FakeModel, registry, broker, decisions: list[Decision]) -> list[dict]:
    """Run one chat turn, answering each approval card with the next decision."""
    events = []
    async for event in stream_chat(model.client, registry, [{"role": "user", "content": "go"}], "m",
                                   approvals=broker, chat_id="chat-1"):
        events.append(event)
        if "approval" in event and decisions:
            broker.resolve(event["approval"]["id"], decisions.pop(0))
    return events


def kinds(events: list[dict]) -> list[str]:
    labels = []
    for event in events:
        if "approval" in event:
            labels.append("approval")
        elif "approval_resolved" in event:
            resolved = event["approval_resolved"]
            labels.append(f"resolved:{resolved['decision']}:{resolved['via']}")
        elif "tool" in event:
            labels.append(f"tool:{event['tool']['status']}")
    return labels


class StreamApprovalTests(PrivateHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.registry = make_registry()
        self.broker = ApprovalBroker()

    def run_turn(self, model: FakeModel, *decisions: Decision) -> list[dict]:
        return asyncio.run(drive(model, self.registry, self.broker, list(decisions)))

    def test_read_tool_runs_without_approval(self):
        model = FakeModel(calls_response(("c", "test__echo", '{"text": "hi"}')), text_response("ok"))
        self.assertEqual(kinds(self.run_turn(model)), ["tool:running", "tool:done"])
        self.assertEqual(EchoSkill.calls, [{"text": "hi"}])

    def test_change_tool_waits_and_runs_on_allow(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"))
        events = self.run_turn(model, Decision("allow", "button"))
        self.assertEqual(kinds(events), ["approval", "resolved:allow:button", "tool:running", "tool:done"])
        card = events[0]["approval"]
        self.assertEqual((card["tool"], card["title"], card["details"]), ("test.write", "Write a test file", {"path": "a.txt"}))
        self.assertEqual(WriteSkill.calls, [{"path": "a.txt"}])
        self.assertNotIn("reply", events[1]["approval_resolved"])

    def test_deny_does_not_run_and_tells_model(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"))
        events = self.run_turn(model, Decision("deny", "button"))
        self.assertEqual(kinds(events), ["approval", "resolved:deny:button", "tool:declined"])
        self.assertEqual(WriteSkill.calls, [])
        self.assertEqual(model.tool_results(1)[0]["error"], DECLINED)

    def test_always_trusts_then_runs_and_next_call_skips_card(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"), calls_response(WRITE), text_response("ok"))
        self.run_turn(model, Decision("always", "button"))
        self.assertIn("test.write", load_trusted())
        self.assertEqual(kinds(self.run_turn(model)), ["tool:running", "tool:done"])
        self.assertEqual(len(WriteSkill.calls), 2)

    def test_other_reply_is_passed_to_model(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"))
        events = self.run_turn(model, Decision("other", "reply", "check the other module first"))
        self.assertEqual(events[1]["approval_resolved"]["reply"], "check the other module first")
        self.assertEqual(WriteSkill.calls, [])
        self.assertEqual(model.tool_results(1)[0]["error"], DECLINED_WITH_REPLY.format(reply="check the other module first"))

    def test_timeout_counts_as_deny(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"))
        with patch.object(permissions, "APPROVAL_TIMEOUT_SECONDS", 0.01):
            events = self.run_turn(model)  # nobody answers
        self.assertEqual(kinds(events), ["approval", "resolved:deny:timeout", "tool:declined"])
        self.assertEqual(model.tool_results(1)[0]["error"], TIMED_OUT)

    def test_two_change_calls_each_ask_in_order(self):
        second = ("call_2", "test__write", '{"path": "b.txt"}')
        model = FakeModel(calls_response(WRITE, second), text_response("ok"))
        events = self.run_turn(model, Decision("deny", "button"), Decision("allow", "button"))
        self.assertEqual(kinds(events), ["approval", "resolved:deny:button", "tool:declined",
                                         "approval", "resolved:allow:button", "tool:running", "tool:done"])
        self.assertEqual(WriteSkill.calls, [{"path": "b.txt"}])
        self.assertEqual([r["success"] for r in model.tool_results(1)], [False, True])

    def test_invalid_arguments_never_ask(self):
        model = FakeModel(calls_response(("c", "test__write", '{"path": ')), text_response("ok"))
        self.assertEqual(kinds(self.run_turn(model)), ["tool:running", "tool:error"])
        self.assertEqual(WriteSkill.calls, [])

    def test_no_broker_refuses_untrusted_change_tool(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"))

        async def run():
            return [e async for e in stream_chat(model.client, self.registry, [{"role": "user", "content": "go"}], "m")]

        self.assertEqual(kinds(asyncio.run(run())), ["tool:declined"])
        self.assertEqual(WriteSkill.calls, [])
        self.assertEqual(model.tool_results(1)[0]["error"], NEEDS_CHAT)

    def test_closing_the_stream_while_pending_leaves_nothing_behind(self):
        model = FakeModel(calls_response(WRITE), text_response("ok"))

        async def run():
            stream = stream_chat(model.client, self.registry, [{"role": "user", "content": "go"}], "m",
                                 approvals=self.broker, chat_id="chat-1")
            async for event in stream:
                if "approval" in event:
                    await stream.aclose()
                    # Checked on the same loop: cleanup must not wait for garbage collection.
                    return self.broker.get(event["approval"]["id"])

        self.assertIsNone(asyncio.run(run()))
        self.assertEqual(WriteSkill.calls, [])


class CompleteChatTests(PrivateHome, unittest.TestCase):
    def test_complete_chat_refuses_untrusted_change_but_runs_trusted(self):
        def turn(*calls):
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
                for call_id, name, arguments in calls
            ]}}]})

        done = httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "finished"}}]})
        registry = make_registry()
        model = FakeModel(turn(WRITE), done)
        self.assertEqual(asyncio.run(complete_chat(model.client, registry, [{"role": "user", "content": "go"}])), "finished")
        self.assertEqual(WriteSkill.calls, [])
        self.assertEqual(model.tool_results(1)[0]["error"], NEEDS_CHAT)

        trust("test.write")
        model = FakeModel(turn(WRITE), done)
        asyncio.run(complete_chat(model.client, registry, [{"role": "user", "content": "go"}]))
        self.assertEqual(WriteSkill.calls, [{"path": "a.txt"}])


if __name__ == "__main__":
    unittest.main()
