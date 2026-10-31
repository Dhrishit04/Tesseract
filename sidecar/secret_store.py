"""Encrypted vault for API keys and tokens at ~/.tesseract/secrets.enc.

The Windows Credential Manager rejects values over 1280 characters, so keyring holds only the AES key
and the secrets themselves live in the vault file.
"""

import base64
import json
import logging
import os
import threading
from typing import Any

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from settings_store import load_settings, save_settings, write_bytes_atomic

logger = logging.getLogger(__name__)

SERVICE = "Tesseract"
NONCE_SIZE = 12
KEY_SIZE = 32
_lock = threading.Lock()  # one writer at a time: every write rewrites the whole file


class SecretStoreError(RuntimeError):
    pass


def vault_path() -> str:
    """Path of the vault file (resolved per call so tests can redirect HOME)."""
    return os.path.join(os.path.expanduser("~"), ".tesseract", "secrets.enc")


def _decode_key(encoded_key: str | None) -> bytes | None:
    """The AES key, or None when it is missing, not base64 or the wrong length."""
    try:
        key = base64.b64decode(encoded_key or "", validate=True)
    except ValueError:  # binascii.Error, or a str key that isn't ASCII
        return None
    return key if len(key) == KEY_SIZE else None


def _load() -> dict:
    """Return every stored secret, or {} when the vault is missing or its key can't open it.

    Raises when the file or the credential store can't be reached, which may pass, so a write can't mistake it for empty.
    """
    try:
        with open(vault_path(), "rb") as vault_file:
            data = vault_file.read()
    except FileNotFoundError:
        return {}
    encoded_key = keyring.get_password(SERVICE, vault_path())
    key = _decode_key(encoded_key)
    if key is None:
        logger.error("The secret vault has no usable key in the credential store")
        return {}
    try:
        secrets = json.loads(AESGCM(key).decrypt(data[:NONCE_SIZE], data[NONCE_SIZE:], None))
    except (InvalidTag, ValueError):
        logger.error("The secret vault could not be decrypted")
        return {}
    return secrets if isinstance(secrets, dict) else {}


def _save(secrets: dict) -> None:
    try:
        encoded_key = keyring.get_password(SERVICE, vault_path())
        key = _decode_key(encoded_key)
        if key is None:
            key = AESGCM.generate_key(KEY_SIZE * 8)
            keyring.set_password(SERVICE, vault_path(), base64.b64encode(key).decode())
    except Exception as error:  # pywintypes.error on Windows, as in _load
        raise SecretStoreError("The system credential store is not available, so this was not saved.") from error
    nonce = os.urandom(NONCE_SIZE)
    try:
        write_bytes_atomic(vault_path(), nonce + AESGCM(key).encrypt(nonce, json.dumps(secrets).encode(), None))
    except OSError as error:
        raise SecretStoreError("Could not write the secrets file, so this was not saved.") from error
    try:
        os.chmod(vault_path(), 0o600)
    except OSError:
        pass


def _load_for_write() -> dict:
    try:
        return _load()
    except Exception as error:  # Windows' credential store raises pywintypes.error, which is neither a KeyringError nor an OSError
        raise SecretStoreError("Could not read the secrets file or the credential store, so this was not saved.") from error


def read_secret(name: str) -> Any | None:
    """get_secret for a value about to be changed and written back: a failed read raises instead of looking empty."""
    return _load_for_write().get(name)


def get_secret(name: str) -> Any | None:
    try:
        return _load().get(name)
    except Exception:  # as in _load_for_write
        logger.error("Could not read the secret vault or its key")
        return None


def set_secret(name: str, value: Any) -> None:
    with _lock:
        _save({**_load_for_write(), name: value})


def delete_secret(name: str) -> None:
    with _lock:
        secrets = _load_for_write()
        if secrets.pop(name, None) is not None:
            _save(secrets)


def migrate_plaintext_secrets() -> None:
    """Move API keys still sitting in settings.json into the vault; a vault value already there wins."""
    settings = load_settings()
    google = settings.get("google")
    fields = [(settings, "openrouter_api_key", "openrouter_api_key")]
    if isinstance(google, dict):
        fields.append((google, "client_secret", "google_client_secret"))
    changed = False
    for holder, field, name in fields:
        if not holder.get(field):
            continue
        try:
            if not read_secret(name):
                set_secret(name, holder[field])
        except SecretStoreError:
            logger.warning("Could not move %s into the vault; it stays in settings.json", name)
            continue
        holder[field] = ""
        changed = True
    if changed:
        save_settings(settings)
