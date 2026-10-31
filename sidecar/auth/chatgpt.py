"""Sign in with ChatGPT, following OpenAI's open-source app flow.

Dynamic registration with PKCE, the ID token checked against OpenAI's JWKS, refresh near expiry and
revocation on sign-out. The account record lives in the secret vault under "chatgpt". Authorize URLs
carry id_token_hint, so URLs, tokens and callback parameters are never logged.
"""

import asyncio
import base64
import hashlib
import logging
import secrets
import time
import uuid
from typing import Mapping, Optional
from urllib.parse import urlencode

import httpx
import jwt

from secret_store import SecretStoreError, get_secret, read_secret, set_secret

logger = logging.getLogger(__name__)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
REVOKE_URL = ISSUER + "/api/accounts/oauth/revoke"  # revocation_endpoint in /.well-known/openid-configuration, 2026-10-24
JWKS_URL = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
REGISTRATION_CLIENT = "dynamic_agent_client"
AGENT_NAME = "Tesseract"
CALLBACK_PATH = "/auth/callback"
ATTEMPT_TTL_SECONDS = 600
REFRESH_MARGIN_SECONDS = 120
UNUSABLE_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}

SECRET_NAME = "chatgpt"
SESSION_FIELDS = ("access_token", "refresh_token", "expires_at")
REVOKE_ATTEMPTS = 3
SIGNED_OUT = "Sign in with ChatGPT in Settings → Models & accounts first."
SIGN_IN_AGAIN = "Your ChatGPT sign-in ended. Continue with ChatGPT to sign in again."
PLAN_NOT_ALLOWED = "ChatGPT plan use isn't allowed for Tesseract yet. Continue with ChatGPT in Settings to allow it."
REFRESH_FAILED = "Couldn't renew the ChatGPT sign-in. Check your connection and try again."
START_AGAIN = "Start again with Continue with ChatGPT in Settings."


class ChatGPTAuthError(RuntimeError):
    pass


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class ChatGPTAccount:
    """The one ChatGPT account this host signs in with."""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport
        self._pending: dict[str, dict] = {}  # state -> attempt, until its callback arrives
        self._lock = asyncio.Lock()
        self._error: Optional[str] = None

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=20.0)

    @staticmethod
    def _record() -> dict:
        record = get_secret(SECRET_NAME)
        return record if isinstance(record, dict) else {}

    @staticmethod
    def _record_to_change() -> dict:
        """The record for a write back, which must never start from a failed read; raises SecretStoreError."""
        record = read_secret(SECRET_NAME)
        return record if isinstance(record, dict) else {}

    # --- Sign-in ----------------------------------------------------------

    def start_sign_in(self, redirect_uri: str, new_account: bool = False) -> str:
        """Begin an attempt and return the authorize URL; raises SecretStoreError if the record can't be read or the host id saved.

        new_account registers afresh (as on a first sign-in), so a different ChatGPT account can replace the saved one.
        """
        record = self._record_to_change()
        if not record.get("ext_agent_host_id"):
            record["ext_agent_host_id"] = f"urn:uuid:{uuid.uuid4()}"
            set_secret(SECRET_NAME, record)
        client_id = None if new_account else record.get("client_id")
        verifier, state, nonce = secrets.token_urlsafe(64), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        self._pending = {s: a for s, a in self._pending.items() if now - a["created"] <= ATTEMPT_TTL_SECONDS}
        self._pending[state] = {"verifier": verifier, "nonce": nonce, "redirect_uri": redirect_uri, "client_id": client_id, "created": now}
        params = {
            "client_id": client_id or REGISTRATION_CLIENT,
            "ext_agent_host_id": record["ext_agent_host_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
        if not client_id:
            params["agent_name_hint"] = AGENT_NAME
        if client_id and record.get("id_token"):
            params["id_token_hint"] = record["id_token"]
        if client_id and record.get("email"):
            params["login_hint"] = record["email"]
        # The owner declined plan use earlier; asking again needs the consent screen back.
        if client_id and "scopes" in record and PLAN_SCOPE not in record["scopes"]:
            params["prompt"] = "consent"
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def finish_sign_in(self, params: Mapping[str, str]) -> tuple[bool, str]:
        """Check the callback, exchange its code and save the account; returns (ok, message for the browser)."""
        attempt = self._pending.pop(params.get("state") or "", None)
        if attempt is None or time.time() - attempt["created"] > ATTEMPT_TTL_SECONDS:
            return False, f"This sign-in link expired or was already used. {START_AGAIN}"
        if params.get("error"):
            return False, f"ChatGPT didn't connect, so nothing was saved. {START_AGAIN}"
        if not params.get("code"):
            return False, f"ChatGPT's reply had no authorization code. {START_AGAIN}"
        client_id, returned_client = attempt["client_id"], params.get("client_id")
        if client_id is None:
            if not returned_client or returned_client == REGISTRATION_CLIENT:
                return False, f"ChatGPT didn't finish registering Tesseract. {START_AGAIN}"
            client_id = returned_client
        elif returned_client and returned_client != client_id:
            return False, f"ChatGPT answered for a different app registration, so nothing was changed. {START_AGAIN}"

        try:
            async with self._http() as http:
                response = await http.post(TOKEN_URL, data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": params["code"],
                    "code_verifier": attempt["verifier"],
                    "redirect_uri": attempt["redirect_uri"],
                    "resource": RESOURCE,
                })
                if response.status_code != 200:
                    logger.warning("ChatGPT code exchange was refused (HTTP %s)", response.status_code)
                    return False, f"ChatGPT didn't accept the sign-in. {START_AGAIN}"
                token = response.json()
                claims = await self._verified_claims(http, token["id_token"], client_id, attempt["nonce"])
            expires_at = time.time() + int(token["expires_in"])
            scopes = (token.get("scope") or params.get("scope") or "").split()
            access_token = token["access_token"]
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError) as error:
            logger.warning("ChatGPT sign-in failed: %s", type(error).__name__)
            return False, f"ChatGPT's sign-in reply couldn't be verified, so nothing was saved. {START_AGAIN}"

        async with self._lock:  # a refresh or sign-out in flight must not overwrite this record, or be overwritten
            try:
                record = self._record_to_change()
                # A returning sign-in must come back as the same account; a registration may bring a new one.
                if attempt["client_id"] and record.get("subject") not in (None, claims["sub"]):
                    return False, ("That's a different ChatGPT account from the one Tesseract uses, so nothing was changed. "
                                   "To switch accounts, sign out in Settings, then choose Use a different account.")
                record.update(
                    client_id=client_id, email=claims.get("email"), subject=claims["sub"], id_token=token["id_token"],
                    access_token=access_token, refresh_token=token.get("refresh_token"), scopes=scopes, expires_at=expires_at,
                )
                set_secret(SECRET_NAME, record)
            except SecretStoreError as error:
                return False, str(error)
        self._error = None
        who = f"{record['email']}'s" if record["email"] else "your"
        if PLAN_SCOPE not in scopes:
            return True, f"Signed in, but ChatGPT plan use wasn't allowed for {who} account. You can allow it from Settings → Models & accounts."
        return True, f"Tesseract can now use {who} ChatGPT plan. You can close this tab."

    @staticmethod
    async def _verified_claims(http: httpx.AsyncClient, id_token: str, client_id: str, nonce: str) -> dict:
        """The ID token's claims once its signature, issuer, audience, expiry and nonce all check out."""
        response = await http.get(JWKS_URL)
        response.raise_for_status()
        key = jwt.PyJWKSet.from_dict(response.json())[jwt.get_unverified_header(id_token).get("kid")]
        claims = jwt.decode(id_token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub"]}, leeway=60)  # desktop clocks drift
        if claims.get("nonce") != nonce:
            raise jwt.InvalidTokenError("nonce mismatch")
        return claims

    # --- Using the account -------------------------------------------------

    def status(self) -> dict:
        record = self._record()
        connected = bool(record.get("access_token"))
        return {
            "connected": connected,
            "email": record.get("email") if connected else None,
            "plan_enabled": connected and PLAN_SCOPE in record.get("scopes", []),
            "error": self._error,
            "registered": bool(record.get("client_id")),
        }

    async def access_token(self) -> str:
        """A current access token for the ChatGPT plan, refreshed first when it is about to expire."""
        record = self._record()
        if not record.get("access_token"):
            raise ChatGPTAuthError(self._error or SIGNED_OUT)
        if PLAN_SCOPE not in record.get("scopes", []):
            raise ChatGPTAuthError(PLAN_NOT_ALLOWED)
        if record["expires_at"] - time.time() > REFRESH_MARGIN_SECONDS:
            return record["access_token"]
        async with self._lock:  # refresh tokens rotate, so only one refresh may be in flight
            record = self._record()  # another caller may have refreshed while this one waited
            if not record.get("access_token"):
                raise ChatGPTAuthError(self._error or SIGNED_OUT)
            if record["expires_at"] - time.time() > REFRESH_MARGIN_SECONDS:
                return record["access_token"]
            return await self._refresh(record)

    async def _refresh(self, record: dict) -> str:
        try:
            async with self._http() as http:
                response = await http.post(TOKEN_URL, data={
                    "grant_type": "refresh_token",
                    "client_id": record["client_id"],
                    "refresh_token": record["refresh_token"],
                    "resource": RESOURCE,
                })
            body = response.json()
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning("ChatGPT token refresh failed: %s", type(error).__name__)
            self._error = REFRESH_FAILED
            raise ChatGPTAuthError(REFRESH_FAILED) from error
        code = body.get("error") if isinstance(body, dict) else None
        code = code.get("code") if isinstance(code, dict) else code
        if code in UNUSABLE_REFRESH:
            logger.warning("ChatGPT refresh token is no longer usable (%s)", code)
            self._save_quietly(remove=SESSION_FIELDS)
            self._error = SIGN_IN_AGAIN
            raise ChatGPTAuthError(SIGN_IN_AGAIN)
        try:
            updates = {
                "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token") or record["refresh_token"],
                "expires_at": time.time() + int(body["expires_in"]),
            }
            if body.get("scope"):
                updates["scopes"] = body["scope"].split()
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            logger.warning("ChatGPT token refresh failed (HTTP %s)", response.status_code)
            self._error = REFRESH_FAILED
            raise ChatGPTAuthError(REFRESH_FAILED) from error
        self._save_quietly(updates)
        self._error = None
        return updates["access_token"]

    def _save_quietly(self, updates: Optional[dict] = None, remove: tuple = ()) -> None:
        """Change the record as saved now, so only these fields change."""
        try:
            record = {**self._record_to_change(), **(updates or {})}
            for field in remove:
                record.pop(field, None)
            set_secret(SECRET_NAME, record)
        except SecretStoreError:
            logger.warning("Could not save the renewed ChatGPT sign-in; the next refresh will ask to sign in again")

    # --- Sign-out -----------------------------------------------------------

    async def sign_out(self) -> bool:
        """Revoke the session, then forget its tokens; returns whether OpenAI confirmed the revocation.

        The issued client_id, host id and account identity stay so a later sign-in reuses the registration.
        Raises SecretStoreError when the cleared record can't be saved.
        """
        async with self._lock:  # a refresh in flight would otherwise save its tokens back after the clear
            record = self._record_to_change()
            revoked = bool(record.get("refresh_token")) and await self._revoke(record)
            if any(field in record for field in (*SESSION_FIELDS, "id_token")):
                for field in (*SESSION_FIELDS, "id_token"):
                    record.pop(field, None)
                set_secret(SECRET_NAME, record)
            self._error = None
            return revoked

    async def _revoke(self, record: dict) -> bool:
        data = {"token": record["refresh_token"], "token_type_hint": "refresh_token", "client_id": record["client_id"]}
        async with self._http() as http:
            for attempt in range(REVOKE_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(0.5 * 2 ** (attempt - 1))
                try:
                    response = await http.post(REVOKE_URL, data=data)
                except httpx.HTTPError:
                    continue
                if response.status_code < 500:  # only outages are worth retrying
                    if response.status_code != 200:
                        logger.warning("ChatGPT sign-out was not confirmed (HTTP %s)", response.status_code)
                    return response.status_code == 200
        logger.warning("Could not reach OpenAI to confirm the ChatGPT sign-out")
        return False


_account: Optional[ChatGPTAccount] = None


def get_chatgpt_account() -> ChatGPTAccount:
    global _account
    if _account is None:
        _account = ChatGPTAccount()
    return _account


def reset_chatgpt_account() -> None:
    """Forget the shared account object (for tests)."""
    global _account
    _account = None
