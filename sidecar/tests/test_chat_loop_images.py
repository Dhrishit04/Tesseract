"""Screenshots reach the model only when the owner turned sharing on, in each provider's own format."""

import asyncio
import base64
import io
import json
import unittest
from typing import Any
from unittest.mock import patch

from PIL import Image

from llm.anthropic_client import to_anthropic
from llm.chat_loop import _gated_skill, _tool_message, complete_chat, stream_chat
from llm.chatgpt_client import to_responses
from llm.images import MAX_SIDE, shrink_screenshot
from llm.openrouter import OpenRouterClient
from skills.desktop_automation import DesktopAutomationScreenshotSkill
from skills.registry import SkillRegistry
from skills.skill_base import SkillBase, SkillResult
from skills.web_automation import WebAutomationScreenshotSkill


def png(width=10, height=10) -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), "red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


IMAGE = {"media_type": "image/jpeg", "data": "abc"}
CALL = {"id": "c", "function": {"name": "snap", "arguments": "{}"}}
HISTORY = [
    {"role": "user", "content": "look"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "snap", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c", "name": "snap", "content": '{"success": true}', "images": [IMAGE]},
]


def sharing(on: bool):
    return patch("llm.chat_loop.load_automation_policy", return_value={"automation_enabled": True, "ocr_enabled": False, "share_screenshots": on})


class ShrinkTests(unittest.TestCase):
    def test_a_big_screenshot_becomes_a_small_jpeg(self):
        image = shrink_screenshot(png(3200, 1600))
        self.assertEqual(image["media_type"], "image/jpeg")
        decoded = Image.open(io.BytesIO(base64.b64decode(image["data"])))
        self.assertEqual(decoded.format, "JPEG")
        self.assertEqual(max(decoded.size), MAX_SIDE)

    def test_damaged_bytes_are_dropped(self):
        self.assertIsNone(shrink_screenshot("iVBORw0KGgo="))


class ToolMessageTests(unittest.TestCase):
    def test_off_means_no_image_at_all(self):
        with sharing(False):
            message = _tool_message(CALL, SkillResult.success({"image_base64": png()}))
        self.assertNotIn("images", message)

    def test_on_attaches_the_image_and_keeps_the_text_small(self):
        data = png()
        with sharing(True):
            message = _tool_message(CALL, SkillResult.success({"image_base64": data, "width": 10}))
        self.assertEqual(len(message["images"]), 1)
        self.assertNotIn(data, message["content"])
        self.assertEqual(json.loads(message["content"])["data"]["width"], 10)


class ProviderFormatTests(unittest.TestCase):
    def test_openrouter_sends_images_in_a_user_turn_after_the_tool_results(self):
        sent = OpenRouterClient(api_key="k")._payload(HISTORY, "m", None, False)["messages"]
        self.assertNotIn("images", sent[2])
        self.assertEqual(sent[3]["role"], "user")
        self.assertEqual(sent[3]["content"][1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}})

    def test_openrouter_keeps_parallel_tool_results_together(self):
        second = {"role": "tool", "tool_call_id": "d", "name": "x", "content": "{}"}
        sent = OpenRouterClient(api_key="k")._payload([*HISTORY, second], "m", None, False)["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "tool", "tool", "user"])

    def test_chatgpt_sends_images_as_input_images(self):
        _, items = to_responses(HISTORY)
        self.assertEqual(items[-2]["type"], "function_call_output")
        self.assertEqual(items[-1]["role"], "user")
        self.assertEqual(items[-1]["content"][1], {"type": "input_image", "image_url": "data:image/jpeg;base64,abc"})

    def test_anthropic_puts_images_inside_the_tool_result(self):
        _, turns = to_anthropic(HISTORY)
        content = turns[-1]["content"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": '{"success": true}'})
        self.assertEqual(content[1], {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc"}})

    def test_no_images_changes_nothing(self):
        plain = [{"role": "user", "content": "hi"}]
        self.assertEqual(OpenRouterClient(api_key="k")._payload(plain, "m", None, False)["messages"], plain)


class SnapSkill(SkillBase):
    kind = "read"

    @property
    def name(self) -> str:
        return "test.snap"

    @property
    def description(self) -> str:
        return "snap"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> SkillResult:
        return SkillResult.success({"image_base64": png()})


class ScriptedClient:
    """Asks for a screenshot twice, then answers, recording what each request carried."""

    def __init__(self):
        self.image_counts: list[int] = []

    def _count(self, messages):
        self.image_counts.append(sum(len(m.get("images", [])) for m in messages))
        return len(self.image_counts)

    async def chat_stream(self, messages, model, tools):
        if self._count(messages) < 3:
            yield {"tool_calls": [{"index": 0, "id": "c", "type": "function", "function": {"name": "test__snap", "arguments": "{}"}}]}
        else:
            yield {"content": "done"}

    async def chat(self, messages, model, tools):
        call = {"id": "c", "type": "function", "function": {"name": "test__snap", "arguments": "{}"}}
        if self._count(messages) < 3:
            return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


class OldScreenshotTests(unittest.TestCase):
    def registry(self):
        registry = SkillRegistry()
        registry._skills = {"test.snap": SnapSkill()}
        registry._loaded = True
        return registry

    def test_only_the_newest_screenshot_is_sent(self):
        client = ScriptedClient()

        async def run():
            async for _ in stream_chat(client, self.registry(), [{"role": "user", "content": "go"}]):
                pass

        with sharing(True):
            asyncio.run(run())
        self.assertEqual(client.image_counts, [0, 1, 1])

    def test_the_non_streaming_loop_does_the_same(self):
        client = ScriptedClient()
        with sharing(True):
            asyncio.run(complete_chat(client, self.registry(), [{"role": "user", "content": "go"}]))
        self.assertEqual(client.image_counts, [0, 1, 1])


class ApprovalTests(unittest.TestCase):
    def gated(self, skill, on):
        registry = SkillRegistry()
        registry._skills = {skill.name: skill}
        registry._loaded = True
        call = {"function": {"name": skill.name, "arguments": "{}"}}
        policy = {"automation_enabled": True, "ocr_enabled": False, "share_screenshots": on}
        with patch("skills.skill_base.load_automation_policy", return_value=policy), patch("llm.chat_loop.load_trusted", return_value=set()):
            return _gated_skill(registry, call)

    def test_a_screenshot_asks_first_only_while_sharing_is_on(self):
        for skill in (DesktopAutomationScreenshotSkill(), WebAutomationScreenshotSkill()):
            with self.subTest(skill=skill.name):
                self.assertIsNone(self.gated(skill, False))
                self.assertIsNotNone(self.gated(skill, True))

    def test_the_card_says_where_it_goes(self):
        self.assertIn("model provider", DesktopAutomationScreenshotSkill().approval_summary({}))
        self.assertIn("model provider", WebAutomationScreenshotSkill().approval_summary({}))


if __name__ == "__main__":
    unittest.main()
