"""The API-key account endpoints and the grouped model list."""

import asyncio
import inspect
import json
import logging
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import anthropic
import httpx
import httpx2
import keyring
import keyring.backends.fail

import server
from auth.chatgpt import reset_chatgpt_account
from llm import providers
from llm.anthropic_client import AnthropicClient
from llm.providers import reset_provider_clients
from secret_store import get_secret, set_secret
from tests.secret_doubles import PrivateVault

MODELS_ERROR = "Could not load {} models. Check the account in Settings → Models & accounts."


class FakeProvider:
    def __init__(self, models=None, error=None):
        self.models, self.error = models or [], error

    async def list_models(self):
        if self.error:
            raise self.error
        return self.models


class AccountEndpointTests(PrivateVault, unittest.TestCase):
    def setUp(self):
        super().setUp()
        reset_provider_clients()
        reset_chatgpt_account()

    def tearDown(self):
        reset_provider_clients()
        reset_chatgpt_account()
        super().tearDown()

    def call(self, method: str, path: str, **kwargs) -> httpx.Response:
        async def main():
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
                return await http.request(method, path, **kwargs)

        return asyncio.run(main())

    def test_put_saves_the_key_and_returns_a_hint(self):
        response = self.call("PUT", "/accounts/openrouter/key", json={"api_key": "sk-or-abcd1234"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"connected": True, "key_hint": "…1234"})
        self.assertEqual(get_secret("openrouter_api_key"), "sk-or-abcd1234")

    def test_wrong_prefix_is_refused(self):
        response = self.call("PUT", "/accounts/openrouter/key", json={"api_key": "nope"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "OpenRouter keys start with sk-or-. Check that you copied the whole key.")
        self.assertIsNone(get_secret("openrouter_api_key"))

    def test_anthropic_key_needs_its_own_prefix(self):
        ok = self.call("PUT", "/accounts/anthropic/key", json={"api_key": "sk-ant-abcd1234"})
        bad = self.call("PUT", "/accounts/anthropic/key", json={"api_key": "sk-or-abcd1234"})

        self.assertEqual((ok.status_code, ok.json()), (200, {"connected": True, "key_hint": "…1234"}))
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(get_secret("anthropic_api_key"), "sk-ant-abcd1234")

    def test_unknown_account_is_404(self):
        response = self.call("PUT", "/accounts/bogus/key", json={"api_key": "x"})

        self.assertEqual((response.status_code, response.json()), (404, {"detail": "Unknown account."}))

    def test_credential_store_failure_is_503(self):
        keyring.set_keyring(keyring.backends.fail.Keyring())

        response = self.call("PUT", "/accounts/openrouter/key", json={"api_key": "sk-or-abcd1234"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("credential store", response.json()["detail"])

    def test_delete_disconnects(self):
        set_secret("openrouter_api_key", "sk-or-abcd1234")

        response = self.call("DELETE", "/accounts/openrouter/key")

        self.assertEqual(response.json(), {"connected": False, "key_hint": None})

    def test_accounts_never_contain_the_full_key(self):
        set_secret("openrouter_api_key", "sk-or-abcd1234")

        body = self.call("GET", "/accounts").text

        self.assertNotIn("sk-or-abcd1234", body)
        self.assertEqual(json.loads(body)["openrouter"], {"connected": True, "key_hint": "…1234"})

    def test_models_are_listed_with_their_provider(self):
        with patch.object(server, "connected_providers", return_value=["openrouter"]), \
                patch.object(server, "get_provider_client", return_value=FakeProvider([{"id": "x/y", "name": "Y"}])):
            body = self.call("GET", "/models").json()

        self.assertEqual(body, {"models": [{"id": "x/y", "name": "Y", "provider": "openrouter"}], "errors": {}})

    def test_failing_provider_reports_a_plain_error(self):
        with patch.object(server, "connected_providers", return_value=["openrouter"]), \
                patch.object(server, "get_provider_client", return_value=FakeProvider(error=RuntimeError("sk-or-secret"))):
            body = self.call("GET", "/models").json()

        self.assertEqual(body, {"models": [], "errors": {"openrouter": MODELS_ERROR.format("OpenRouter")}})

    def test_rejected_anthropic_key_is_reported(self):
        def rejected(request):
            return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

        http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(rejected))
        client = AnthropicClient("sk-ant-bad", base_url="http://anthropic.test", http_client=http)
        with patch.dict(providers._clients, {"anthropic": client}):
            response = self.call("GET", "/models")

        self.assertEqual(response.json(), {"models": [], "errors": {"anthropic": MODELS_ERROR.format("Anthropic")}})
        self.assertNotIn("x-api-key", response.text)

    def test_sign_in_route_runs_on_the_event_loop(self):
        # start_sign_in changes state that finish_sign_in reads on the loop, so it must not run on a worker thread.
        self.assertTrue(inspect.iscoroutinefunction(server.chatgpt_sign_in))

    def test_no_keys_means_no_models(self):
        self.assertEqual(self.call("GET", "/models").json(), {"models": [], "errors": {}})

    def test_chatgpt_sign_in_always_calls_back_to_127_0_0_1(self):
        async def main():
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8765") as http:
                return await http.post("/accounts/chatgpt/sign-in")

        response = asyncio.run(main())

        self.assertEqual(response.status_code, 200)
        redirect = parse_qs(urlparse(response.json()["auth_url"]).query)["redirect_uri"]
        self.assertEqual(redirect, ["http://127.0.0.1:8765/auth/callback"])

    def test_chatgpt_sign_in_can_start_a_new_account(self):
        set_secret("chatgpt", {"ext_agent_host_id": "urn:uuid:h", "client_id": "oaiapp_1", "email": "me@example.com", "id_token": "idt"})

        def sign_in_query(**kwargs) -> dict:
            return parse_qs(urlparse(self.call("POST", "/accounts/chatgpt/sign-in", **kwargs).json()["auth_url"]).query)

        returning, new = sign_in_query(), sign_in_query(json={"new_account": True})

        self.assertEqual(returning["client_id"], ["oaiapp_1"])
        self.assertEqual(new["client_id"], ["dynamic_agent_client"])
        self.assertNotIn("login_hint", new)
        self.assertEqual(new["ext_agent_host_id"], ["urn:uuid:h"])

    def test_chatgpt_sign_in_without_a_credential_store_is_503(self):
        keyring.set_keyring(keyring.backends.fail.Keyring())

        response = self.call("POST", "/accounts/chatgpt/sign-in")

        self.assertEqual(response.status_code, 503)
        self.assertIn("credential store", response.json()["detail"])

    def test_bad_chatgpt_callback_echoes_nothing(self):
        response = self.call("GET", "/auth/callback", params={"state": "bad", "code": "zq-code-77", "error_description": "<b>zq</b>"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("ChatGPT sign-in failed", response.text)
        for value in ("bad", "zq-code-77", "zq"):
            self.assertNotIn(value, response.text)

    def test_accounts_include_chatgpt(self):
        self.assertEqual(
            self.call("GET", "/accounts").json()["chatgpt"],
            {"connected": False, "email": None, "plan_enabled": False, "error": None, "registered": False},
        )

    def test_chatgpt_sign_out_when_signed_out(self):
        self.assertEqual(self.call("POST", "/accounts/chatgpt/sign-out").json(), {"revoked": False})

    def test_access_log_hides_query_strings(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1:5", "GET", "/auth/callback?code=secret&state=s", "1.1", 200), None)

        for log_filter in logging.getLogger("uvicorn.access").filters:
            log_filter.filter(record)

        self.assertNotIn("secret", record.getMessage())
        self.assertIn("/auth/callback", record.getMessage())

    def test_chat_without_the_providers_key_says_what_to_connect(self):
        response = self.call("POST", "/chat/stream", json={"message": "hi", "model": "anthropic:c"})

        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and "DONE" not in line]
        self.assertEqual(events[0]["code"], "no_api_key")
        self.assertEqual(events[0]["message"], "Connect Anthropic in Settings → Models & accounts to use this model.")


if __name__ == "__main__":
    unittest.main()
