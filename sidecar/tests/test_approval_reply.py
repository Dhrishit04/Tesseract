"""Turning a typed answer to an approval card into allow, always, deny or other."""

import asyncio
import unittest

import httpx

from llm.approval_reply import CLASSIFY_MARKER, classify_reply
from permissions import ApprovalRequest
from tests.approval_doubles import FakeModel

REQUEST = ApprovalRequest(id="a1", chat_id="c1", tool="gmail.send_email", title="Send an email",
                          summary="To priya@example.com: Lunch", details={}, irreversible=True, model="m")


def answered(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def classify(*responses: httpx.Response, text: str = "yes honey") -> tuple[str, FakeModel]:
    model = FakeModel(*responses)
    return asyncio.run(classify_reply(model.client, "m", REQUEST, text)), model


class ClassifyReplyTests(unittest.TestCase):
    def test_yes_honey_is_allow(self):
        self.assertEqual(classify(answered("allow"))[0], "allow")

    def test_parses_word_with_punctuation_and_case(self):
        self.assertEqual(classify(answered("Always."))[0], "always")

    def test_sentence_answer_is_other(self):
        self.assertEqual(classify(answered("I think they mean yes"))[0], "other")

    def test_empty_answer_is_other(self):
        self.assertEqual(classify(answered(""))[0], "other")

    def test_http_error_is_other(self):
        self.assertEqual(classify(httpx.Response(500, text="boom"))[0], "other")

    def test_malformed_body_is_other(self):
        self.assertEqual(classify(httpx.Response(200, json={"choices": []}))[0], "other")

    def test_prompt_carries_marker_and_action(self):
        _, model = classify(answered("allow"))
        body = model.requests[0]
        system, user = body["messages"]
        self.assertIn(CLASSIFY_MARKER, system["content"])
        self.assertIn("Send an email", system["content"])
        # The summary is built from model-written arguments, so it must not steer the classifier.
        self.assertNotIn("priya@example.com", system["content"])
        self.assertEqual(user, {"role": "user", "content": "yes honey"})
        self.assertNotIn("tools", body)
        self.assertFalse(body["stream"])


if __name__ == "__main__":
    unittest.main()
