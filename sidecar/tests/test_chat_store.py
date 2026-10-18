"""SQLite chat store: chats, messages, search, titles and recovery from a damaged file."""

import glob
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import patch

import chat_store
from chat_store import ChatStore, derive_title


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "tesseract.db")
        self.store = ChatStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_and_list_newest_first(self):
        first = self.store.create_chat("m", "A")
        time.sleep(0.01)
        second = self.store.create_chat("m", "B")
        time.sleep(0.01)
        self.store.add_message(first["id"], "user", "bump")

        self.assertEqual([chat["id"] for chat in self.store.list_chats()], [first["id"], second["id"]])

    def test_get_chat_returns_messages_in_order(self):
        chat = self.store.create_chat("m")
        self.store.add_message(chat["id"], "user", "hi")
        self.store.add_message(chat["id"], "assistant", "hello", tools=[{"name": "filesystem.read_file", "status": "done"}])

        messages = self.store.get_chat(chat["id"])["messages"]

        self.assertEqual([(m["role"], m["content"]) for m in messages], [("user", "hi"), ("assistant", "hello")])
        self.assertEqual(messages[1]["tools"], [{"name": "filesystem.read_file", "status": "done"}])
        self.assertEqual(messages[0]["tools"], [])

    def test_history_excludes_empty_assistant_turns(self):
        chat = self.store.create_chat("m")
        self.store.add_message(chat["id"], "user", "hi")
        self.store.add_message(chat["id"], "assistant", "")

        self.assertEqual(self.store.history(chat["id"]), [{"role": "user", "content": "hi"}])

    def test_search_matches_title_and_message_text(self):
        q3 = self.store.create_chat("m", "Q3")
        self.store.add_message(q3["id"], "user", "Budget sheet please")
        self.store.create_chat("m", "Holiday")

        self.assertEqual([chat["id"] for chat in self.store.list_chats("budget")], [q3["id"]])
        self.assertEqual([chat["title"] for chat in self.store.list_chats("HOLI")], ["Holiday"])

    def test_update_and_delete(self):
        chat = self.store.create_chat("m", "old")
        self.store.add_message(chat["id"], "user", "x")

        self.store.update_chat(chat["id"], title="new")
        self.store.update_chat(chat["id"], model="other")
        fetched = self.store.get_chat(chat["id"])
        self.assertEqual((fetched["title"], fetched["model"]), ("new", "other"))

        self.assertTrue(self.store.delete_chat(chat["id"]))
        self.assertIsNone(self.store.get_chat(chat["id"]))
        self.assertFalse(self.store.delete_chat(chat["id"]))
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertEqual(raw.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_derive_title(self):
        self.assertEqual(derive_title("  \n\n  Plan my week\nwith details"), "Plan my week")
        self.assertEqual(derive_title("x" * 100), "x" * 59 + "…")
        self.assertEqual(derive_title("   "), "New chat")
        self.assertEqual(derive_title("🎉🎉"), "🎉🎉")
        self.assertEqual(derive_title("a   lot\tof   space"), "a lot of space")

    def test_corrupt_db_is_set_aside(self):
        self.store.close()
        with open(self.path, "wb") as damaged:
            damaged.write(b"not a database" * 100)

        recovered = ChatStore(self.path)
        try:
            self.assertEqual(recovered.list_chats(), [])
            self.assertEqual(len(glob.glob(self.path + ".corrupt-*")), 1)
            self.assertTrue(recovered.create_chat("m")["id"])
        finally:
            recovered.close()


    def test_locked_database_is_not_treated_as_corrupt(self):
        # A fresh database that another connection is writing to: switching it to WAL needs a lock.
        self.store.close()
        for leftover in glob.glob(self.path + "*"):
            os.remove(leftover)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as holder:
            holder.execute("CREATE TABLE other (x)")
            holder.execute("BEGIN EXCLUSIVE")
            holder.execute("INSERT INTO other VALUES (1)")
            with self.assertRaises(sqlite3.OperationalError):
                ChatStore(self.path, busy_timeout=0.1)
            holder.execute("ROLLBACK")

        self.assertEqual(glob.glob(self.path + ".corrupt-*"), [])
        self.store = ChatStore(self.path)


class SharedStoreTests(unittest.TestCase):
    def test_threads_get_one_shared_store(self):
        with tempfile.TemporaryDirectory() as home, patch("os.path.expanduser", return_value=home):
            chat_store._store = None
            stores = []
            threads = [threading.Thread(target=lambda: stores.append(chat_store.get_chat_store())) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            try:
                self.assertEqual(len({id(store) for store in stores}), 1)
            finally:
                chat_store._store.close()
                chat_store._store = None


if __name__ == "__main__":
    unittest.main()
