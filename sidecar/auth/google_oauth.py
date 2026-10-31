"""Google OAuth 2.0 authentication manager for Tesseract.

Handles the OAuth flow (with PKCE and state checking), refresh, and revocation.
Tokens and the client secret live in the secret vault.
"""

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from secret_store import SecretStoreError, delete_secret, get_secret, set_secret

logger = logging.getLogger(__name__)

# OAuth 2.0 scopes for Gmail and Calendar
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES

# Redirect URI for local callback
REDIRECT_URI = "http://localhost:8765/oauth/callback"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

# Where earlier versions kept encrypted tokens (read once by migrate_legacy_tokens)
TOKENS_DIR = Path.home() / ".tesseract" / "tokens"
TOKEN_FILENAME = "google_tokens.enc"
KEY_FILENAME = "encryption_key"
AES_KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12


@dataclass
class OAuthStatus:
    """Status of Google OAuth connection."""
    connected: bool
    email: Optional[str] = None
    scopes: list[str] = field(default_factory=list)
    expires_at: Optional[float] = None
    error: Optional[str] = None


class GoogleOAuthManager:
    """Manages Google OAuth 2.0 authentication for Tesseract."""

    def __init__(self, tokens_dir: Path = TOKENS_DIR):
        self._tokens_dir = Path(tokens_dir)
        self._credentials: Optional[Credentials] = None
        self._client_id: Optional[str] = None
        self._client_secret: Optional[str] = None
        self._pending_flow: Optional[Flow] = None
        self._pending_state: Optional[str] = None
        self._email: Optional[str] = None

    @property
    def _token_file(self) -> Path:
        return self._tokens_dir / TOKEN_FILENAME

    @property
    def _key_file(self) -> Path:
        return self._tokens_dir / KEY_FILENAME

    def migrate_legacy_tokens(self) -> None:
        """Move tokens from the old encrypted files into the vault, then delete the files."""
        if not self._token_file.exists() or not self._key_file.exists():
            return
        try:
            key = self._key_file.read_bytes()
            blob = self._token_file.read_bytes()
            if len(key) == AES_KEY_BYTES:
                plaintext = AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], None)
            else:  # the oldest versions used Fernet
                plaintext = Fernet(key).decrypt(blob)
            token_data = json.loads(plaintext.decode())
            set_secret("google_tokens", token_data)
        except (InvalidTag, InvalidToken, ValueError, OSError, SecretStoreError):
            # Leave the files so a later start can retry; a damaged file must never stop the sidecar.
            logger.exception("Could not move stored Google tokens into the vault")
            return
        try:
            self._token_file.unlink(missing_ok=True)
            self._key_file.unlink(missing_ok=True)
        except OSError:
            # The tokens are already in the vault; a locked file must not stop the sidecar starting.
            logger.exception("Could not delete the old Google token files")

    # --- Configuration ----------------------------------------------------

    def configure(self, client_id: str, client_secret: str = "") -> None:
        """Configure OAuth client credentials; an empty secret keeps the one in the vault."""
        if client_secret:
            set_secret("google_client_secret", client_secret)
        self._client_id = client_id
        self._client_secret = client_secret or get_secret("google_client_secret")

    def is_configured(self) -> bool:
        """Check if OAuth client is configured."""
        return bool(self._client_id and self._client_secret)

    def load_settings(self, settings: dict) -> None:
        """Load OAuth client settings from the full settings dict."""
        google_settings = settings.get("google", {})
        client_id = google_settings.get("client_id", "")
        client_secret = get_secret("google_client_secret")
        if client_id and client_secret:
            self._client_id, self._client_secret = client_id, client_secret
        else:
            self._client_id = self._client_secret = None

    def _new_flow(self) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uris": [REDIRECT_URI],
                    "auth_uri": AUTH_URI,
                    "token_uri": TOKEN_URI,
                }
            },
            scopes=ALL_SCOPES,
            redirect_uri=REDIRECT_URI,
        )

    # --- OAuth flow -------------------------------------------------------

    def get_authorization_url(self) -> str:
        """Start a sign-in and return the Google consent URL.

        The flow object is kept until the callback because it holds the PKCE
        code verifier that Google requires during the token exchange.
        """
        if not self.is_configured():
            raise ValueError("Google OAuth not configured. Set client_id and client_secret in settings.")

        flow = self._new_flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # Force consent to get refresh token
        )
        self._pending_flow = flow
        self._pending_state = state
        return auth_url

    def handle_callback(self, code: str, state: Optional[str]) -> OAuthStatus:
        """Exchange the authorization code from Google's redirect for tokens."""
        if not self.is_configured():
            return OAuthStatus(connected=False, error="Google OAuth not configured")
        flow = self._pending_flow
        if flow is None:
            return OAuthStatus(connected=False, error="No sign-in in progress. Start again from Settings.")
        if not state or not secrets.compare_digest(state, self._pending_state or ""):
            return OAuthStatus(connected=False, error="Sign-in state did not match. Start again from Settings.")

        self._pending_flow = None
        self._pending_state = None
        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            logger.exception("OAuth token exchange failed")
            return OAuthStatus(connected=False, error=str(exc))

        self._credentials = flow.credentials
        self._email = None
        self._store_credentials()
        return self._connected_status()

    # --- Token storage ----------------------------------------------------

    def _store_credentials(self) -> None:
        """Save credentials to the vault."""
        if not self._credentials:
            return
        token_data = {
            "token": self._credentials.token,
            "refresh_token": self._credentials.refresh_token,
            "token_uri": self._credentials.token_uri,
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
            "scopes": list(self._credentials.scopes or []),
            "expiry": self._credentials.expiry.isoformat() if self._credentials.expiry else None,
        }
        try:
            set_secret("google_tokens", token_data)
        except SecretStoreError:
            logger.exception("Could not save Google tokens; they will be lost when Tesseract closes")

    def load_credentials(self) -> bool:
        """Load credentials from the vault."""
        token_data = get_secret("google_tokens")
        if not isinstance(token_data, dict):
            return False

        credentials = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes"),
        )
        if token_data.get("expiry"):
            credentials.expiry = datetime.fromisoformat(token_data["expiry"])
        self._credentials = credentials
        if credentials.expired:
            self._refresh_credentials()
        return credentials.valid

    def _refresh_credentials(self) -> bool:
        """Refresh an expired access token using the refresh token."""
        if not self._credentials or not self._credentials.refresh_token:
            return False
        try:
            self._credentials.refresh(Request())
        except Exception:
            logger.exception("Google token refresh failed")
            return False
        self._store_credentials()
        return True

    # --- Status -----------------------------------------------------------

    def get_credentials(self) -> Optional[Credentials]:
        """Return valid credentials, refreshing them first if they expired."""
        if not self._credentials:
            return None
        if self._credentials.expired:
            self._refresh_credentials()
        return self._credentials if self._credentials.valid else None

    def _lookup_email(self) -> Optional[str]:
        """Read the account address via the Gmail profile (covered by gmail.readonly)."""
        if self._email:
            return self._email
        try:
            service = build("gmail", "v1", credentials=self._credentials, cache_discovery=False)
            self._email = service.users().getProfile(userId="me").execute().get("emailAddress")
        except Exception as exc:
            logger.warning("Could not read Google account address: %s", exc)
        return self._email

    def _connected_status(self) -> OAuthStatus:
        credentials = self._credentials
        expiry = credentials.expiry if credentials else None
        return OAuthStatus(
            connected=True,
            email=self._lookup_email(),
            scopes=list((credentials.scopes if credentials else None) or []),
            expires_at=expiry.timestamp() if expiry else None,
        )

    def get_status(self) -> OAuthStatus:
        """Get current OAuth connection status."""
        if not self.is_configured():
            return OAuthStatus(connected=False, error="Not configured")
        if self._credentials is None:
            self.load_credentials()
        if self.get_credentials():
            return self._connected_status()
        if self._credentials:
            return OAuthStatus(connected=False, error="Token expired, re-authentication required")
        return OAuthStatus(connected=False, error="Not authenticated")

    async def disconnect(self) -> OAuthStatus:
        """Revoke Google access and remove stored tokens."""
        credentials, self._credentials, self._email = self._credentials, None, None
        token = credentials and (credentials.refresh_token or credentials.token)
        if token:
            await _revoke_token(token)
        try:
            delete_secret("google_tokens")
        except SecretStoreError as exc:
            logger.exception("Error removing stored Google tokens")
            return OAuthStatus(connected=False, error=str(exc))
        return OAuthStatus(connected=False, error="Disconnected")

    # --- Services ---------------------------------------------------------

    def get_gmail_service(self):
        """Get authenticated Gmail API service."""
        creds = self.get_credentials()
        if not creds:
            raise ValueError("Google account not connected. Connect it in Settings.")
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def get_calendar_service(self):
        """Get authenticated Calendar API service."""
        creds = self.get_credentials()
        if not creds:
            raise ValueError("Google account not connected. Connect it in Settings.")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)


async def _revoke_token(token: str) -> None:
    """Ask Google to revoke a token. Failure is logged; local tokens are removed regardless."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                REVOKE_URI,
                params={"token": token},
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Token revocation request failed: %s", exc)


# Global instance
_oauth_manager: Optional[GoogleOAuthManager] = None


def get_google_oauth_manager() -> GoogleOAuthManager:
    """Get the global Google OAuth manager instance."""
    global _oauth_manager
    if _oauth_manager is None:
        _oauth_manager = GoogleOAuthManager()
    return _oauth_manager


def reset_oauth_manager() -> None:
    """Reset the global OAuth manager (useful for testing)."""
    global _oauth_manager
    _oauth_manager = None
