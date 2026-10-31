"""Sign in with ChatGPT against a fake auth.openai.com (no network, in-memory credential store)."""

import asyncio
import base64
import hashlib
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import keyring
import keyring.backends.fail
from cryptography.hazmat.primitives.asymmetric import rsa

from auth.chatgpt import (
    ATTEMPT_TTL_SECONDS,
    ISSUER,
    PLAN_SCOPE,
    RESOURCE,
    SCOPES,
    ChatGPTAccount,
    ChatGPTAuthError,
)
from secret_store import SecretStoreError, get_secret, set_secret
from tests.secret_doubles import PrivateVault

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {"keys": [{**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}]}
REDIRECT = "http://127.0.0.1:8765/auth/callback"


def query(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlparse(url).query).items()}


class FakeOpenAI:
    """Routes auth.openai.com requests by path and records each form it receives."""

    def __init__(self):
        self.forms: list[tuple[str, dict]] = []
        self.sub, self.email, self.scope, self.nonce = "user-1", "me@example.com", SCOPES, None
        self.signing_key, self.id_claims = KEY, {}
        self.issued = 0
        self.token = self.grant
        self.revoke = lambda: httpx.Response(200)
        self.gate: asyncio.Event | None = None  # when set, token replies wait until it opens

    def id_token(self, client_id: str) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": client_id, "sub": self.sub, "email": self.email, "nonce": self.nonce,
                  "iat": now, "exp": now + 3600, **self.id_claims}
        return jwt.encode(claims, self.signing_key, algorithm="RS256", headers={"kid": "k1"})

    def grant(self, form: dict) -> httpx.Response:
        self.issued += 1
        body = {"access_token": f"at-{self.issued}", "refresh_token": f"rt-{self.issued}", "token_type": "Bearer",
                "expires_in": 3600, "id_token": self.id_token(form["client_id"])}
        if self.scope is not None:
            body["scope"] = self.scope
        return httpx.Response(200, json=body)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json=JWKS)
        form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
        self.forms.append((request.url.path, form))
        if request.url.path == "/api/accounts/oauth/token":
            await asyncio.sleep(0)  # yield like a real network wait, so concurrent callers really overlap
            if self.gate:
                await self.gate.wait()
            return self.token(form)
        if request.url.path == "/api/accounts/oauth/revoke":
            return self.revoke()
        return httpx.Response(404)


class ChatGPTAccountTests(PrivateVault, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeOpenAI()
        self.account = ChatGPTAccount(transport=httpx.MockTransport(self.fake.handle))

    def record(self) -> dict:
        return get_secret("chatgpt") or {}

    def sign_in(self, new_account: bool = False, **callback) -> tuple[tuple[bool, str], dict]:
        """Start a sign-in, then answer its callback; callback=None drops a parameter."""
        url_query = query(self.account.start_sign_in(REDIRECT, new_account=new_account))
        self.fake.nonce = url_query["nonce"]
        params = {"code": "code-1", "state": url_query["state"], "client_id": "oaiapp_1", **callback}
        params = {name: value for name, value in params.items() if value is not None}
        return asyncio.run(self.account.finish_sign_in(params)), url_query

    def test_first_sign_in_url(self):
        first = query(self.account.start_sign_in(REDIRECT))
        second = query(self.account.start_sign_in(REDIRECT))

        self.assertEqual(first["client_id"], "dynamic_agent_client")
        self.assertEqual(first["agent_name_hint"], "Tesseract")
        self.assertTrue(first["ext_agent_host_id"].startswith("urn:uuid:"))
        self.assertEqual(first["scope"], SCOPES)
        self.assertEqual(first["resource"], RESOURCE)
        self.assertEqual(first["redirect_uri"], REDIRECT)
        self.assertEqual(first["response_type"], "code")
        self.assertEqual(first["code_challenge_method"], "S256")
        verifier = self.account._pending[first["state"]]["verifier"]
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(len(first["code_challenge"]), 43)
        self.assertEqual(first["code_challenge"], digest)
        self.assertNotEqual(first["state"], second["state"])
        self.assertNotEqual(first["nonce"], second["nonce"])
        self.assertEqual(first["ext_agent_host_id"], second["ext_agent_host_id"])
        self.assertEqual(self.record()["ext_agent_host_id"], first["ext_agent_host_id"])
        self.assertNotIn("id_token_hint", first)
        self.assertNotIn("prompt", first)

    def test_registration_callback_stores_tokens(self):
        (ok, message), url_query = self.sign_in()

        self.assertTrue(ok, message)
        self.assertIn("me@example.com", message)
        path, form = self.fake.forms[0]
        self.assertEqual(path, "/api/accounts/oauth/token")
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["client_id"], "oaiapp_1")
        self.assertEqual(form["code"], "code-1")
        digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(digest, url_query["code_challenge"])
        self.assertEqual(form["redirect_uri"], REDIRECT)
        self.assertEqual(form["resource"], RESOURCE)
        record = self.record()
        self.assertEqual(record["client_id"], "oaiapp_1")
        self.assertEqual(record["email"], "me@example.com")
        self.assertEqual(record["subject"], "user-1")
        self.assertEqual((record["access_token"], record["refresh_token"]), ("at-1", "rt-1"))
        self.assertTrue(record["id_token"])
        self.assertEqual(record["ext_agent_host_id"], url_query["ext_agent_host_id"])
        self.assertIn(PLAN_SCOPE, record["scopes"])
        self.assertAlmostEqual(record["expires_at"], time.time() + 3600, delta=5)
        self.assertEqual(self.account.status(), {"connected": True, "email": "me@example.com", "plan_enabled": True, "error": None, "registered": True})
        self.assertEqual(asyncio.run(self.account.access_token()), "at-1")

    def test_small_clock_skew_is_accepted(self):
        self.fake.id_claims = {"iat": int(time.time()) + 30}  # OpenAI's clock a little ahead of ours

        (ok, message), _ = self.sign_in()

        self.assertTrue(ok, message)

    def test_scope_falls_back_to_the_callback(self):
        self.fake.scope = None

        (ok, _), _ = self.sign_in(scope=SCOPES)

        self.assertTrue(ok)
        self.assertIn(PLAN_SCOPE, self.record()["scopes"])

    def test_rejections_store_nothing(self):
        def unknown_state():
            self.account.start_sign_in(REDIRECT)
            return asyncio.run(self.account.finish_sign_in({"code": "c", "state": "bogus", "client_id": "oaiapp_1"}))

        def expired():
            url_query = query(self.account.start_sign_in(REDIRECT))
            with patch("time.time", return_value=time.time() + ATTEMPT_TTL_SECONDS + 1):
                return asyncio.run(self.account.finish_sign_in({"code": "c", "state": url_query["state"], "client_id": "oaiapp_1"}))

        def other_key():
            self.fake.signing_key = OTHER_KEY

        def token_400():
            self.fake.token = lambda form: httpx.Response(400, json={"error": "invalid_grant"})

        cases = {
            "unknown state": unknown_state,
            "expired attempt": expired,
            "access denied": lambda: self.sign_in(error="access_denied", code=None)[0],
            "registration without client_id": lambda: self.sign_in(client_id=None)[0],
            "bad signature": lambda: (other_key(), self.sign_in()[0])[1],
            "wrong aud": lambda: (self.fake.id_claims.update(aud="oaiapp_other"), self.sign_in()[0])[1],
            "wrong nonce": lambda: (self.fake.id_claims.update(nonce="not-the-nonce"), self.sign_in()[0])[1],
            "token endpoint 400": lambda: (token_400(), self.sign_in()[0])[1],
        }
        for name, case in cases.items():
            with self.subTest(name):
                self.fake = FakeOpenAI()
                self.account = ChatGPTAccount(transport=httpx.MockTransport(self.fake.handle))
                ok, message = case()
                self.assertFalse(ok)
                self.assertTrue(message)
                self.assertNotIn("access_token", self.record())
                self.assertFalse(self.account.status()["connected"])

    def test_store_unavailable_fails_the_sign_in(self):
        url_query = query(self.account.start_sign_in(REDIRECT))
        self.fake.nonce = url_query["nonce"]
        keyring.set_keyring(keyring.backends.fail.Keyring())

        ok, message = asyncio.run(self.account.finish_sign_in({"code": "c", "state": url_query["state"], "client_id": "oaiapp_1"}))

        self.assertFalse(ok)
        self.assertIn("credential store", message)

    def test_grant_without_plan_scope(self):
        self.fake.scope = "openid profile email offline_access"

        (ok, message), _ = self.sign_in()

        self.assertTrue(ok)
        self.assertIn("ChatGPT plan use wasn't allowed", message)
        self.assertEqual(self.record()["access_token"], "at-1")
        self.assertFalse(self.account.status()["plan_enabled"])
        with self.assertRaises(ChatGPTAuthError) as raised:
            asyncio.run(self.account.access_token())
        self.assertIn("ChatGPT plan use isn't allowed", str(raised.exception))
        self.assertEqual(query(self.account.start_sign_in(REDIRECT))["prompt"], "consent")

    def test_signed_out_access_token_says_where_to_sign_in(self):
        with self.assertRaises(ChatGPTAuthError) as raised:
            asyncio.run(self.account.access_token())

        self.assertEqual(str(raised.exception), "Sign in with ChatGPT in Settings → Models & accounts first.")

    def test_returning_sign_in(self):
        self.sign_in()
        saved = self.record()

        (ok, _), url_query = self.sign_in(client_id="oaiapp_2")

        self.assertEqual(url_query["client_id"], "oaiapp_1")
        self.assertEqual(url_query["id_token_hint"], saved["id_token"])
        self.assertEqual(url_query["login_hint"], "me@example.com")
        self.assertEqual(url_query["ext_agent_host_id"], saved["ext_agent_host_id"])
        self.assertNotIn("agent_name_hint", url_query)
        self.assertFalse(ok)
        self.assertEqual(self.record(), saved)

        self.fake.sub = "user-2"
        (ok, _), _ = self.sign_in()

        self.assertFalse(ok)
        self.assertEqual(self.record(), saved)

        self.fake.sub = "user-1"
        (ok, _), _ = self.sign_in(client_id=None)  # a reauthorization callback may omit client_id

        self.assertTrue(ok)
        self.assertEqual(self.record()["client_id"], "oaiapp_1")

    def test_new_account_url(self):
        self.sign_in()
        host_id = self.record()["ext_agent_host_id"]

        url_query = query(self.account.start_sign_in(REDIRECT, new_account=True))

        self.assertEqual(url_query["client_id"], "dynamic_agent_client")
        self.assertEqual(url_query["agent_name_hint"], "Tesseract")
        self.assertEqual(url_query["ext_agent_host_id"], host_id)
        for name in ("id_token_hint", "login_hint", "prompt"):
            self.assertNotIn(name, url_query)

    def test_new_account_replaces_the_registration(self):
        self.sign_in()
        asyncio.run(self.account.sign_out())
        host_id = self.record()["ext_agent_host_id"]
        self.assertTrue(self.account.status()["registered"])
        self.fake.sub, self.fake.email = "user-2", "other@example.com"

        (ok, message), _ = self.sign_in()  # an ordinary returning sign-in still refuses another account

        self.assertFalse(ok)
        self.assertEqual(message, "That's a different ChatGPT account from the one Tesseract uses, so nothing was changed. "
                                  "To switch accounts, sign out in Settings, then choose Use a different account.")
        self.assertEqual(self.record()["subject"], "user-1")

        (ok, message), _ = self.sign_in(new_account=True, client_id="oaiapp_2")

        self.assertTrue(ok, message)
        record = self.record()
        self.assertEqual((record["client_id"], record["subject"], record["email"]), ("oaiapp_2", "user-2", "other@example.com"))
        self.assertEqual(record["ext_agent_host_id"], host_id)
        self.assertEqual(self.account.status()["email"], "other@example.com")

    def save_expiring_record(self):
        set_secret("chatgpt", {
            "ext_agent_host_id": "urn:uuid:host", "client_id": "oaiapp_1", "email": "me@example.com", "subject": "user-1",
            "id_token": "idt", "access_token": "at-old", "refresh_token": "rt-old", "scopes": SCOPES.split(),
            "expires_at": time.time() + 30,
        })

    def failing_read(self, on_call: int = 1):
        """The credential store fails once, on that read, then recovers."""
        real, calls = keyring.get_password, []

        def get_password(service, username):
            calls.append(username)
            if len(calls) == on_call:
                raise RuntimeError("busy")
            return real(service, username)

        return patch("keyring.get_password", get_password)

    def test_a_failed_read_keeps_the_saved_record(self):
        self.save_expiring_record()
        saved = self.record()
        with self.failing_read(), self.assertRaises(SecretStoreError):
            self.account.start_sign_in(REDIRECT)
        with self.failing_read(), self.assertRaises(SecretStoreError):
            asyncio.run(self.account.sign_out())
        self.assertEqual(self.record(), saved)

    def test_a_failed_read_during_refresh_keeps_the_registration(self):
        self.save_expiring_record()
        with self.failing_read(on_call=3):  # the two token checks read first
            self.assertEqual(asyncio.run(self.account.access_token()), "at-1")
        self.assertEqual(self.record()["client_id"], "oaiapp_1")

    def test_a_failed_read_when_the_refresh_token_died_keeps_the_registration(self):
        self.save_expiring_record()
        self.fake.token = lambda form: httpx.Response(400, json={"error": "refresh_token_reused"})
        with self.failing_read(on_call=3), self.assertRaises(ChatGPTAuthError):
            asyncio.run(self.account.access_token())
        self.assertEqual(self.record()["client_id"], "oaiapp_1")

    def test_a_failed_read_at_the_callback_keeps_the_saved_record(self):
        self.save_expiring_record()
        saved = self.record()
        url_query = query(self.account.start_sign_in(REDIRECT))
        self.fake.nonce = url_query["nonce"]
        with self.failing_read():
            ok, message = asyncio.run(self.account.finish_sign_in({"state": url_query["state"], "code": "c"}))
        self.assertFalse(ok)
        self.assertIn("was not saved", message)
        self.assertEqual(self.record(), saved)

    def test_refresh(self):
        self.save_expiring_record()

        async def two_callers():
            return await asyncio.gather(self.account.access_token(), self.account.access_token())

        self.assertEqual(asyncio.run(two_callers()), ["at-1", "at-1"])
        self.assertEqual(len(self.fake.forms), 1)
        _, form = self.fake.forms[0]
        self.assertEqual(form, {"grant_type": "refresh_token", "client_id": "oaiapp_1", "refresh_token": "rt-old", "resource": RESOURCE})
        record = self.record()
        self.assertEqual((record["access_token"], record["refresh_token"]), ("at-1", "rt-1"))
        self.assertAlmostEqual(record["expires_at"], time.time() + 3600, delta=5)

    def test_sign_out_waits_for_a_refresh_in_flight(self):
        self.save_expiring_record()

        async def race():
            self.fake.gate = asyncio.Event()
            refresh = asyncio.create_task(self.account.access_token())
            while not self.fake.forms:
                await asyncio.sleep(0)
            sign_out = asyncio.create_task(self.account.sign_out())
            await asyncio.sleep(0)
            self.fake.gate.set()
            return await asyncio.gather(refresh, sign_out)

        asyncio.run(race())

        self.assertFalse(self.account.status()["connected"])
        record = self.record()
        for field in ("access_token", "refresh_token", "id_token"):
            self.assertNotIn(field, record)
        self.assertEqual(self.fake.forms[-1], ("/api/accounts/oauth/revoke", {"token": "rt-1", "token_type_hint": "refresh_token", "client_id": "oaiapp_1"}))

    def test_unusable_refresh_token_signs_out(self):
        self.save_expiring_record()
        self.fake.token = lambda form: httpx.Response(400, json={"error": "refresh_token_reused"})

        with self.assertRaises(ChatGPTAuthError):
            asyncio.run(self.account.access_token())

        record = self.record()
        self.assertNotIn("access_token", record)
        self.assertNotIn("refresh_token", record)
        self.assertEqual((record["client_id"], record["ext_agent_host_id"]), ("oaiapp_1", "urn:uuid:host"))
        status = self.account.status()
        self.assertFalse(status["connected"])
        self.assertIn("sign in again", status["error"])

    def test_refresh_outage_keeps_the_tokens(self):
        self.save_expiring_record()
        self.fake.token = lambda form: httpx.Response(503)

        with self.assertRaises(ChatGPTAuthError):
            asyncio.run(self.account.access_token())

        record = self.record()
        self.assertEqual((record["access_token"], record["refresh_token"]), ("at-old", "rt-old"))

    def test_sign_out(self):
        self.sign_in()

        revoked = asyncio.run(self.account.sign_out())

        self.assertTrue(revoked)
        path, form = self.fake.forms[-1]
        self.assertEqual(path, "/api/accounts/oauth/revoke")
        self.assertEqual(form, {"token": "rt-1", "token_type_hint": "refresh_token", "client_id": "oaiapp_1"})
        record = self.record()
        for field in ("access_token", "refresh_token", "id_token"):
            self.assertNotIn(field, record)
        self.assertEqual(record["client_id"], "oaiapp_1")
        self.assertTrue(record["ext_agent_host_id"].startswith("urn:uuid:"))
        self.assertFalse(self.account.status()["connected"])
        url_query = query(self.account.start_sign_in(REDIRECT))
        self.assertNotIn("id_token_hint", url_query)
        self.assertEqual(url_query["client_id"], "oaiapp_1")

    def test_sign_out_without_network_still_clears(self):
        self.sign_in()

        def offline():
            raise httpx.ConnectError("offline")

        self.fake.revoke = offline
        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            revoked = asyncio.run(self.account.sign_out())

        self.assertFalse(revoked)
        self.assertEqual(sum(path.endswith("/revoke") for path, _ in self.fake.forms), 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [0.5, 1.0])
        self.assertNotIn("refresh_token", self.record())


if __name__ == "__main__":
    unittest.main()
