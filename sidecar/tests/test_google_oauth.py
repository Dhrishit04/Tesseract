"""Tests for the Google OAuth manager and its HTTP endpoints (no network)."""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

import server
from auth import google_oauth
from auth.google_oauth import GoogleOAuthManager


def make_credentials(expired: bool = False) -> Credentials:
    credentials = Credentials(
        token="access-token",
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client",
        client_secret="secret",
        scopes=google_oauth.ALL_SCOPES,
    )
    offset = timedelta(hours=-1) if expired else timedelta(hours=1)
    credentials.expiry = (datetime.now(timezone.utc) + offset).replace(tzinfo=None)
    return credentials


class FakeFlow:
    """Stands in for google_auth_oauthlib Flow; enforces PKCE like Google does."""

    instances: list["FakeFlow"] = []

    def __init__(self):
        self.code_verifier = None
        self.credentials = None
        FakeFlow.instances.append(self)

    @classmethod
    def from_client_config(cls, *_args, **_kwargs):
        return cls()

    def authorization_url(self, **_kwargs):
        self.code_verifier = "verifier-123"
        return "https://accounts.google.com/o/oauth2/auth?state=state-abc", "state-abc"

    def fetch_token(self, code):
        if not self.code_verifier:
            raise ValueError("invalid_grant: Missing code verifier")
        self.credentials = make_credentials()


def gmail_service(email: str) -> MagicMock:
    service = MagicMock()
    service.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": email}
    return service


class GoogleOAuthManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tokens_dir = Path(self.tmp.name)
        self.manager = GoogleOAuthManager(tokens_dir=self.tokens_dir)
        self.manager.configure("client", "secret")
        FakeFlow.instances = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_callback_reuses_the_flow_that_issued_the_url(self):
        with patch.object(google_oauth, "Flow", FakeFlow), \
                patch.object(google_oauth, "build", return_value=gmail_service("me@example.com")):
            self.manager.get_authorization_url()
            status = self.manager.handle_callback("code-1", "state-abc")

        self.assertTrue(status.connected, status.error)
        self.assertEqual(status.email, "me@example.com")
        self.assertEqual(len(FakeFlow.instances), 1)

    def test_callback_rejects_mismatched_state(self):
        with patch.object(google_oauth, "Flow", FakeFlow):
            self.manager.get_authorization_url()
            status = self.manager.handle_callback("code-1", "forged")

        self.assertFalse(status.connected)
        self.assertIn("state", status.error.lower())

    def test_tokens_round_trip_with_256_bit_key(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()

        reloaded = GoogleOAuthManager(tokens_dir=self.tokens_dir)
        reloaded.configure("client", "secret")
        self.assertTrue(reloaded.load_credentials())
        self.assertEqual(reloaded._credentials.token, "access-token")
        self.assertEqual(len((self.tokens_dir / "encryption_key").read_bytes()), 32)
        self.assertNotIn(b"access-token", (self.tokens_dir / "google_tokens.enc").read_bytes())

    def test_corrupt_token_file_is_reported_not_raised(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()
        token_file = self.tokens_dir / "google_tokens.enc"
        token_file.write_bytes(token_file.read_bytes()[:-1] + b"x")  # damaged GCM tag

        fresh = GoogleOAuthManager(tokens_dir=self.tokens_dir)
        fresh.configure("client", "secret")
        self.assertFalse(fresh.load_credentials())
        self.assertFalse(fresh.get_status().connected)

    def test_clearing_client_details_unconfigures(self):
        self.manager.load_settings({"google": {"client_id": "", "client_secret": ""}})

        self.assertFalse(self.manager.is_configured())

    def test_legacy_fernet_tokens_still_load(self):
        key = Fernet.generate_key()
        (self.tokens_dir / "encryption_key").write_bytes(key)
        legacy = (
            '{"token": "old", "refresh_token": "r", "token_uri": "https://oauth2.googleapis.com/token",'
            ' "client_id": "client", "client_secret": "secret", "scopes": [],'
            f' "expiry": "{(datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()}"}}'
        )
        (self.tokens_dir / "google_tokens.enc").write_bytes(Fernet(key).encrypt(legacy.encode()))

        self.assertTrue(self.manager.load_credentials())
        self.assertEqual(self.manager._credentials.token, "old")

    def test_expired_credentials_are_refreshed_before_use(self):
        credentials = make_credentials(expired=True)

        def refresh(_request):
            credentials.token = "fresh-token"
            credentials.expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

        credentials.refresh = refresh
        self.manager._credentials = credentials

        self.assertEqual(self.manager.get_credentials().token, "fresh-token")

    def test_disconnect_revokes_token_and_deletes_files(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()

        with patch.object(google_oauth, "_revoke_token", new=AsyncMock()) as revoke:
            asyncio.run(self.manager.disconnect())

        revoke.assert_awaited_once_with("refresh-token")
        self.assertFalse((self.tokens_dir / "google_tokens.enc").exists())


class OAuthCallbackEndpointTests(unittest.TestCase):
    def test_error_callback_renders_page_instead_of_redirect_loop(self):
        client = TestClient(server.app, base_url="http://127.0.0.1")

        response = client.get("/oauth/callback?error=access_denied", follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("access_denied", response.text)


if __name__ == "__main__":
    unittest.main()
