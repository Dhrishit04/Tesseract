"""Tests for the Google OAuth manager and its HTTP endpoints (no network)."""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import keyring
import keyring.backends.fail
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

import server
from auth import google_oauth
from auth.google_oauth import GoogleOAuthManager
from secret_store import get_secret, set_secret
from tests.secret_doubles import PrivateVault


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


class GoogleOAuthManagerTests(PrivateVault, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.tokens_dir = Path(self.tmp.name)
        self.manager = GoogleOAuthManager(tokens_dir=self.tokens_dir)
        self.manager.configure("client", "secret")
        FakeFlow.instances = []

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

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

    def test_tokens_round_trip_through_vault(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()

        reloaded = GoogleOAuthManager(tokens_dir=self.tokens_dir)
        reloaded.configure("client", "secret")
        self.assertTrue(reloaded.load_credentials())
        self.assertEqual(reloaded._credentials.token, "access-token")

    def test_corrupt_token_file_is_reported_not_raised(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()
        set_secret("google_tokens", "damaged")

        fresh = GoogleOAuthManager(tokens_dir=self.tokens_dir)
        fresh.configure("client", "secret")
        self.assertFalse(fresh.load_credentials())
        self.assertFalse(fresh.get_status().connected)

    def test_clearing_client_details_unconfigures(self):
        self.manager.load_settings({"google": {"client_id": "", "client_secret": ""}})

        self.assertFalse(self.manager.is_configured())

    def legacy_tokens(self) -> str:
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        return (
            '{"token": "old", "refresh_token": "r", "token_uri": "https://oauth2.googleapis.com/token",'
            f' "client_id": "client", "client_secret": "secret", "scopes": [], "expiry": "{expiry}"}}'
        )

    def write_legacy_files(self, key: bytes, blob: bytes):
        (self.tokens_dir / "encryption_key").write_bytes(key)
        (self.tokens_dir / "google_tokens.enc").write_bytes(blob)

    def test_tokens_saved_in_vault_not_files(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()

        self.assertEqual(list(self.tokens_dir.iterdir()), [])
        self.assertEqual(get_secret("google_tokens")["refresh_token"], "refresh-token")

    def test_legacy_token_files_migrate_once(self):
        aes_key = AESGCM.generate_key(bit_length=256)
        nonce = b"n" * 12
        aes_blob = nonce + AESGCM(aes_key).encrypt(nonce, self.legacy_tokens().encode(), None)
        fernet_key = Fernet.generate_key()
        for key, blob in ((aes_key, aes_blob), (fernet_key, Fernet(fernet_key).encrypt(self.legacy_tokens().encode()))):
            with self.subTest(key_length=len(key)):
                self.write_legacy_files(key, blob)

                self.manager.migrate_legacy_tokens()

                self.assertEqual(get_secret("google_tokens")["token"], "old")
                self.assertEqual(list(self.tokens_dir.iterdir()), [])
                set_secret("google_tokens", {"token": "newer"})
                self.manager.migrate_legacy_tokens()
                self.assertEqual(get_secret("google_tokens"), {"token": "newer"})

    def test_legacy_files_stay_when_vault_unavailable(self):
        fernet_key = Fernet.generate_key()
        self.write_legacy_files(fernet_key, Fernet(fernet_key).encrypt(self.legacy_tokens().encode()))
        keyring.set_keyring(keyring.backends.fail.Keyring())

        self.manager.migrate_legacy_tokens()

        self.assertEqual(len(list(self.tokens_dir.iterdir())), 2)

    def test_locked_legacy_files_do_not_stop_startup(self):
        fernet_key = Fernet.generate_key()
        self.write_legacy_files(fernet_key, Fernet(fernet_key).encrypt(self.legacy_tokens().encode()))

        with patch.object(Path, "unlink", side_effect=PermissionError("in use")), \
                self.assertLogs("auth.google_oauth", level="ERROR"):
            self.manager.migrate_legacy_tokens()

        self.assertEqual(get_secret("google_tokens")["token"], "old")

    def test_client_secret_comes_from_vault(self):
        set_secret("google_client_secret", "vault-secret")
        fresh = GoogleOAuthManager(tokens_dir=self.tokens_dir)

        fresh.load_settings({"google": {"client_id": "id"}})

        self.assertTrue(fresh.is_configured())

    def test_expired_credentials_are_refreshed_before_use(self):
        credentials = make_credentials(expired=True)

        def refresh(_request):
            credentials.token = "fresh-token"
            credentials.expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

        credentials.refresh = refresh
        self.manager._credentials = credentials

        self.assertEqual(self.manager.get_credentials().token, "fresh-token")

    def test_disconnect_revokes_token_and_deletes_vault_tokens(self):
        self.manager._credentials = make_credentials()
        self.manager._store_credentials()

        with patch.object(google_oauth, "_revoke_token", new=AsyncMock()) as revoke:
            asyncio.run(self.manager.disconnect())

        revoke.assert_awaited_once_with("refresh-token")
        self.assertIsNone(get_secret("google_tokens"))


class OAuthCallbackEndpointTests(unittest.TestCase):
    def test_error_callback_renders_page_instead_of_redirect_loop(self):
        client = TestClient(server.app, base_url="http://127.0.0.1")

        response = client.get("/oauth/callback?error=access_denied", follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("access_denied", response.text)


if __name__ == "__main__":
    unittest.main()
