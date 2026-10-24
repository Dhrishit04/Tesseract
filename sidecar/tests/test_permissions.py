"""Trust list in ~/.tesseract/permissions.json and the broker that holds pending approvals."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import permissions
import settings_store
from permissions import ApprovalBroker, Decision, describe_approval, load_trusted, revoke, trust
from tests.test_permissions_classification import Unclassified


def open_request(broker: ApprovalBroker, chat_id: str = "chat-1"):
    return broker.open(chat_id=chat_id, tool="test.write", title="Write a test file", summary="s",
                       details={}, irreversible=False, model="m")


class TrustStoreTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.home_patch = patch("os.path.expanduser", return_value=self.home.name)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.home.cleanup()

    def test_trust_round_trip(self):
        trust("gmail.send_email")
        self.assertEqual(load_trusted(), {"gmail.send_email"})
        self.assertTrue(revoke("gmail.send_email"))
        self.assertEqual(load_trusted(), set())

    def test_revoke_unknown_returns_false(self):
        self.assertFalse(revoke("nothing.here"))

    def test_corrupt_file_reads_as_empty_and_next_trust_rewrites_it(self):
        path = Path(permissions.permissions_path())
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertLogs("permissions", "ERROR"):
            self.assertEqual(load_trusted(), set())
        trust("x")
        self.assertEqual(load_trusted(), {"x"})

    def test_trust_survives_settings_save(self):
        trust("gmail.send_email")
        settings_store.save_settings({"model": "anything"})
        self.assertEqual(load_trusted(), {"gmail.send_email"})


class BrokerTests(unittest.TestCase):
    def test_resolve_then_wait_returns_decision(self):
        async def run():
            broker = ApprovalBroker()
            request = open_request(broker)
            self.assertTrue(broker.resolve(request.id, Decision("allow", "button")))
            return await broker.wait(request.id), broker.get(request.id)

        decision, leftover = asyncio.run(run())
        self.assertEqual(decision, Decision("allow", "button"))
        self.assertIsNone(leftover)

    def test_second_resolve_is_refused(self):
        async def run():
            broker = ApprovalBroker()
            request = open_request(broker)
            return broker.resolve(request.id, Decision("allow", "button")), broker.resolve(request.id, Decision("deny", "button"))

        self.assertEqual(asyncio.run(run()), (True, False))

    def test_wait_times_out_as_deny(self):
        async def run():
            broker = ApprovalBroker()
            request = open_request(broker)
            return await broker.wait(request.id, timeout=0.01), broker.get(request.id)

        decision, leftover = asyncio.run(run())
        self.assertEqual(decision, Decision("deny", "timeout"))
        self.assertIsNone(leftover)

    def test_default_timeout_is_read_at_call_time(self):
        async def run():
            broker = ApprovalBroker()
            request = open_request(broker)
            with patch.object(permissions, "APPROVAL_TIMEOUT_SECONDS", 0.01):
                return await broker.wait(request.id)

        self.assertEqual(asyncio.run(run()), Decision("deny", "timeout"))

    def test_cancelled_wait_removes_entry(self):
        async def run():
            broker = ApprovalBroker()
            request = open_request(broker)
            waiting = asyncio.create_task(broker.wait(request.id))
            await asyncio.sleep(0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            return broker.get(request.id), broker.resolve(request.id, Decision("allow", "button"))

        self.assertEqual(asyncio.run(run()), (None, False))


    def test_public_view_leaves_out_chat_and_model(self):
        async def run():
            return open_request(ApprovalBroker()).public()

        self.assertEqual(set(asyncio.run(run())), {"id", "tool", "title", "summary", "details", "irreversible"})


class DescribeApprovalTests(unittest.TestCase):
    def test_describe_approval_truncates_long_strings(self):
        title, summary, details = describe_approval(Unclassified(), {"content": "a" * 5000, "nested": {"x": "b" * 5000}})
        self.assertEqual(len(details["content"]), 4001)
        self.assertTrue(details["content"].endswith("…"))
        self.assertEqual(len(details["nested"]["x"]), 4001)
        self.assertEqual(title, "test.unclassified")  # no approval_title: fall back to the name
        self.assertEqual(summary, "A skill that declares no kind.")


if __name__ == "__main__":
    unittest.main()
