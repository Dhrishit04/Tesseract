"""Test doubles for the secret vault: an in-memory credential store and a private HOME that uses it."""

import tempfile
from unittest.mock import patch

import keyring
import keyring.backend
import keyring.errors


class PrivateHome:
    """Mixin: point ~ at a temporary folder so the trust list and chats never touch the real ones."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.home_patch = patch("os.path.expanduser", return_value=self.home.name)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.home.cleanup()


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


class PrivateVault(PrivateHome):
    """Mixin: PrivateHome plus a MemoryKeyring (self.keyring), so tests never touch the real credential store."""

    def setUp(self):
        super().setUp()
        self.previous_keyring = keyring.get_keyring()
        self.keyring = MemoryKeyring()
        keyring.set_keyring(self.keyring)

    def tearDown(self):
        keyring.set_keyring(self.previous_keyring)
        super().tearDown()
