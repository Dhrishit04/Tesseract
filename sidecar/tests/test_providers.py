"""Provider routing and the streamed-chunk adapter contract."""

import asyncio
import unittest
from unittest import mock

import httpx

from llm.chat_loop import collect_stream
from llm.chatgpt_client import ChatGPTClient
from llm.openrouter import OpenRouterClient
from llm.providers import get_provider_client, model_ref, parse_model, reset_provider_clients
from secret_store import set_secret
from tests.secret_doubles import PrivateVault


class ParseModelTests(unittest.TestCase):
    def test_parse_model(self):
        cases = {
            "anthropic:claude-opus-5-5": ("anthropic", "claude-opus-5-5"),
            "chatgpt:gpt-6.1-sol": ("chatgpt", "gpt-6.1-sol"),
            "openrouter:openai/gpt-4o": ("openrouter", "openai/gpt-4o"),
            "meta-llama/llama-3-8b-instruct:free": ("openrouter", "meta-llama/llama-3-8b-instruct:free"),
            "mock/fast": ("openrouter", "mock/fast"),
        }
        for ref, expected in cases.items():
            self.assertEqual(parse_model(ref), expected)

    def test_model_ref_is_bare_for_openrouter(self):
        self.assertEqual(model_ref("openrouter", "x/y"), "x/y")
        self.assertEqual(model_ref("anthropic", "c"), "anthropic:c")


class ProviderClientTests(PrivateVault, unittest.TestCase):
    def setUp(self):
        super().setUp()
        reset_provider_clients()

    def tearDown(self):
        reset_provider_clients()
        super().tearDown()

    def test_no_client_without_a_key(self):
        self.assertIsNone(get_provider_client("openrouter"))

    def test_client_is_cached_until_reset(self):
        set_secret("openrouter_api_key", "sk-or-1")
        first = get_provider_client("openrouter")
        self.assertIsInstance(first, OpenRouterClient)
        self.assertEqual(first.api_key, "sk-or-1")
        self.assertIs(get_provider_client("openrouter"), first)
        reset_provider_clients()
        self.assertIsNot(get_provider_client("openrouter"), first)

    def test_chatgpt_needs_a_connected_plan_enabled_account(self):
        class Account:
            def __init__(self, **status):
                self._status = status

            def status(self):
                return self._status

        for status, expect_client in (({"connected": False, "plan_enabled": False}, False),
                                      ({"connected": True, "plan_enabled": False}, False),
                                      ({"connected": True, "plan_enabled": True}, True)):
            reset_provider_clients()
            with mock.patch("llm.providers.get_chatgpt_account", return_value=Account(**status)):
                client = get_provider_client("chatgpt")
            self.assertEqual(isinstance(client, ChatGPTClient), expect_client, status)


class OpenRouterListModelsTests(unittest.TestCase):
    def client(self, handler) -> OpenRouterClient:
        return OpenRouterClient("sk-or-1", transport=httpx.MockTransport(handler))

    def test_returns_the_catalog(self):
        client = self.client(lambda r: httpx.Response(200, json={"data": [{"id": "x/y"}]}))
        self.assertEqual(asyncio.run(client.list_models()), [{"id": "x/y"}])

    def test_rejected_key_raises(self):
        client = self.client(lambda r: httpx.Response(401))
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client.list_models())

    def test_network_failure_raises(self):
        def down(request):
            raise httpx.ConnectError("down", request=request)

        client = self.client(down)
        with self.assertRaises(httpx.ConnectError):
            asyncio.run(client.list_models())


async def chunks(*items):
    for item in items:
        yield item


class CollectStreamTests(unittest.TestCase):
    def test_collect_stream_assembles_text_calls_and_replay(self):
        fragments = [
            {"index": 0, "id": "c1", "type": "function", "function": {"name": "a__b", "arguments": '{"x"'}},
            {"index": 0, "function": {"arguments": ": 1}"}},
        ]
        result = asyncio.run(collect_stream(chunks(
            {"content": "Hi "}, {"content": "there"}, {"tool_calls": fragments}, {"replay": ["r"]})))
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "Hi there")
        self.assertEqual(message["tool_calls"], [{"id": "c1", "type": "function", "function": {"name": "a__b", "arguments": '{"x": 1}'}}])
        self.assertEqual(message["replay"], ["r"])

    def test_error_chunk_short_circuits(self):
        error = {"error": True, "message": "boom"}
        self.assertEqual(asyncio.run(collect_stream(chunks({"content": "a"}, error, {"content": "b"}))), error)


if __name__ == "__main__":
    unittest.main()
