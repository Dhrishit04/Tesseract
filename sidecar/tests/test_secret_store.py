import base64
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import keyring
import keyring.backends.fail

from secret_store import (
    SERVICE, SecretStoreError, delete_secret, get_secret, migrate_plaintext_secrets, set_secret, vault_path,
)
from settings_store import settings_path, write_bytes_atomic
from tests.secret_doubles import MemoryKeyring, PrivateVault


class SecretStoreTests(PrivateVault, unittest.TestCase):
    def test_round_trips_strings_and_dicts(self):
        set_secret("a", "sk-1")
        set_secret("b", {"x": 1})
        self.assertEqual(get_secret("a"), "sk-1")
        self.assertEqual(get_secret("b"), {"x": 1})
        self.assertIsNone(get_secret("missing"))

    def test_file_holds_no_plaintext(self):
        set_secret("a", "sk-or-SECRET")
        with open(vault_path(), "rb") as vault_file:
            data = vault_file.read()
        self.assertNotIn(b"sk-or-SECRET", data)
        self.assertNotIn(b'"a"', data)

    def test_key_is_in_keyring_under_vault_path(self):
        set_secret("a", "sk-1")
        self.assertTrue(keyring.get_password(SERVICE, vault_path()))
        self.assertEqual(list(self.keyring.store), [(SERVICE, vault_path())])

    def test_delete_removes_only_that_secret(self):
        set_secret("a", 1)
        set_secret("b", 2)
        delete_secret("a")
        self.assertIsNone(get_secret("a"))
        self.assertEqual(get_secret("b"), 2)

    def test_unreadable_vault_reads_empty(self):
        set_secret("a", 1)
        other_key = base64.b64encode(os.urandom(32)).decode()
        keyring.set_password(SERVICE, vault_path(), other_key)
        with self.assertLogs("secret_store", level="ERROR"):
            self.assertIsNone(get_secret("a"))
        set_secret("c", 1)
        self.assertEqual(get_secret("c"), 1)

    def test_no_credential_store(self):
        keyring.set_keyring(keyring.backends.fail.Keyring())
        with self.assertRaises(SecretStoreError):
            set_secret("a", "x")
        self.assertIsNone(get_secret("a"))
        self.assertFalse(os.path.exists(vault_path()))

    def test_credential_store_own_errors_are_contained(self):
        class PyWinTypesError(Exception):
            """Like pywintypes.error: neither a KeyringError nor an OSError."""

        class BrokenKeyring(MemoryKeyring):
            def get_password(self, service, username):
                raise PyWinTypesError("credential manager unavailable")

        set_secret("a", 1)
        keyring.set_keyring(BrokenKeyring())
        with self.assertLogs("secret_store", level="ERROR"):
            self.assertIsNone(get_secret("a"))
        with self.assertRaisesRegex(SecretStoreError, "was not saved"):
            set_secret("b", 1)

    def test_non_ascii_stored_key_reads_empty(self):
        set_secret("a", 1)
        keyring.set_password(SERVICE, vault_path(), "clé")
        with self.assertLogs("secret_store", level="ERROR"):
            self.assertIsNone(get_secret("a"))

    def test_failed_vault_write_is_a_secret_store_error(self):
        with patch("secret_store.write_bytes_atomic", side_effect=PermissionError("locked")), \
                self.assertRaisesRegex(SecretStoreError, "^Could not write the secrets file, so this was not saved.$"):
            set_secret("a", "x")

    def test_a_failed_read_never_replaces_the_vault(self):
        set_secret("a", 1)
        set_secret("b", 2)
        stored_key = keyring.get_password(SERVICE, vault_path())
        for write in (lambda: set_secret("c", 3), lambda: delete_secret("a")):
            # Each failure clears after the read, so only the read can stop the write.
            with patch("keyring.get_password", side_effect=[RuntimeError("busy"), stored_key]), \
                    self.assertRaises(SecretStoreError):
                write()
            with patch("secret_store.open", create=True, side_effect=PermissionError("locked")), \
                    self.assertRaises(SecretStoreError):
                write()
        self.assertEqual((get_secret("a"), get_secret("b"), get_secret("c")), (1, 2, None))


class WriteBytesAtomicTests(unittest.TestCase):
    def test_write_bytes_atomic_replaces_whole_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "f.bin")
            write_bytes_atomic(path, b"one")
            write_bytes_atomic(path, b"two")
            with open(path, "rb") as written:
                self.assertEqual(written.read(), b"two")
            self.assertEqual(os.listdir(folder), ["f.bin"])


class SecretStoreRepairTests(PrivateVault, unittest.TestCase):
    def test_bad_stored_key_is_replaced_on_next_write(self):
        for bad_key in ("!!not base64!!", base64.b64encode(b"short").decode()):
            with self.subTest(bad_key=bad_key):
                set_secret("a", 1)
                keyring.set_password(SERVICE, vault_path(), bad_key)
                with self.assertLogs("secret_store", level="ERROR"):
                    self.assertIsNone(get_secret("a"))
                set_secret("c", 2)
                self.assertEqual(get_secret("c"), 2)

    def test_concurrent_writers_lose_no_update(self):
        def write(name):
            for number in range(50):
                set_secret(name, number)

        threads = [threading.Thread(target=write, args=(name,)) for name in ("x", "y")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((get_secret("x"), get_secret("y")), (49, 49))


class MigrationTests(PrivateVault, unittest.TestCase):
    def write_settings(self, settings):
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as settings_file:
            json.dump(settings, settings_file)

    def read_settings(self):
        with open(settings_path(), encoding="utf-8") as settings_file:
            return json.load(settings_file)

    def test_migration_moves_settings_secrets(self):
        self.write_settings({"openrouter_api_key": "sk-or-1", "google": {"client_id": "id", "client_secret": "gs"}, "theme": "dark"})
        migrate_plaintext_secrets()
        saved = self.read_settings()
        self.assertEqual(saved["openrouter_api_key"], "")
        self.assertEqual(saved["google"], {"client_id": "id", "client_secret": ""})
        self.assertEqual(saved["theme"], "dark")
        self.assertEqual(get_secret("openrouter_api_key"), "sk-or-1")
        self.assertEqual(get_secret("google_client_secret"), "gs")

    def test_migration_keeps_newer_vault_value(self):
        set_secret("openrouter_api_key", "sk-or-new")
        self.write_settings({"openrouter_api_key": "sk-or-old"})
        migrate_plaintext_secrets()
        self.assertEqual(get_secret("openrouter_api_key"), "sk-or-new")
        self.assertEqual(self.read_settings()["openrouter_api_key"], "")

    def test_migration_keeps_the_vault_value_through_a_failed_read(self):
        set_secret("openrouter_api_key", "sk-or-new")
        self.write_settings({"openrouter_api_key": "sk-or-old"})
        stored_key = keyring.get_password(SERVICE, vault_path())
        with patch("keyring.get_password", side_effect=[RuntimeError("busy"), stored_key, stored_key]), \
                self.assertLogs("secret_store", level="WARNING"):
            migrate_plaintext_secrets()
        self.assertEqual(get_secret("openrouter_api_key"), "sk-or-new")
        self.assertEqual(self.read_settings()["openrouter_api_key"], "sk-or-old")  # retried next start

    def test_migration_without_credential_store_leaves_file_alone(self):
        keyring.set_keyring(keyring.backends.fail.Keyring())
        self.write_settings({"openrouter_api_key": "sk-or-1", "google": {"client_secret": "gs"}})
        with open(settings_path(), "rb") as settings_file:
            before = settings_file.read()
        with self.assertLogs("secret_store", level="WARNING") as logs:
            migrate_plaintext_secrets()
        with open(settings_path(), "rb") as settings_file:
            self.assertEqual(settings_file.read(), before)
        self.assertNotIn("sk-or-1", "".join(logs.output))
