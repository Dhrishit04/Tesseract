"""Saved conversations in a local SQLite database (~/.tesseract/tesseract.db)."""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

TITLE_LIMIT = 60
FALLBACK_TITLE = "New chat"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_chat ON messages(chat_id, created_at);
"""


def db_path() -> str:
    """Database location (resolved per call so tests can redirect HOME)."""
    return os.path.join(os.path.expanduser("~"), ".tesseract", "tesseract.db")


def derive_title(text: str) -> str:
    """First non-empty line of a prompt, whitespace collapsed, at most 60 characters."""
    for line in text.splitlines():
        title = " ".join(line.split())
        if title:
            return title if len(title) <= TITLE_LIMIT else title[: TITLE_LIMIT - 1] + "…"
    return FALLBACK_TITLE


def _chat(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "title", "model", "created_at", "updated_at")}


def _message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "role": row["role"],
        "content": row["content"],
        "tools": json.loads(row["tools_json"] or "[]"),
        "created_at": row["created_at"],
    }


class ChatStore:
    def __init__(self, path: Optional[str] = None, busy_timeout: float = 5.0):
        self.path = path or db_path()
        self._busy_timeout = busy_timeout
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            self._conn = self._open()
        except sqlite3.OperationalError:
            raise  # locked or unavailable: a temporary condition, never a reason to discard the file
        except sqlite3.DatabaseError:
            # A damaged database must not stop the sidecar; keep it aside for inspection.
            aside = f"{self.path}.corrupt-{int(time.time())}"
            logger.exception("Chat database is unreadable; moving it to %s", aside)
            os.replace(self.path, aside)
            self._conn = self._open()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self._busy_timeout, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_chat(self, model: str, title: str = "") -> dict:
        now = time.time()
        chat_id = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chats (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, title, model, now, now),
            )
        return {"id": chat_id, "title": title, "model": model, "created_at": now, "updated_at": now}

    def list_chats(self, query: str = "") -> list[dict]:
        sql = "SELECT * FROM chats"
        params: tuple = ()
        if query.strip():
            pattern = f"%{query.strip().lower()}%"
            sql += (
                " WHERE lower(title) LIKE ? OR id IN"
                " (SELECT chat_id FROM messages WHERE lower(content) LIKE ?)"
            )
            params = (pattern, pattern)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY updated_at DESC", params).fetchall()
        return [_chat(row) for row in rows]

    def get_chat(self, chat_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
            if row is None:
                return None
            messages = self._conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at, rowid", (chat_id,)
            ).fetchall()
        return {**_chat(row), "messages": [_message(message) for message in messages]}

    def update_chat(self, chat_id: str, *, title: Optional[str] = None, model: Optional[str] = None) -> Optional[dict]:
        changes = {key: value for key, value in (("title", title), ("model", model)) if value is not None}
        if changes:
            assignments = ", ".join(f"{key} = ?" for key in changes)
            with self._lock, self._conn:
                self._conn.execute(f"UPDATE chats SET {assignments} WHERE id = ?", (*changes.values(), chat_id))
        chat = self.get_chat(chat_id)
        if chat is None:
            return None
        chat.pop("messages")
        return chat

    def delete_chat(self, chat_id: str) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,)).rowcount > 0

    def add_message(self, chat_id: str, role: str, content: str, tools: Optional[list[dict]] = None) -> dict:
        now = time.time()
        message = {"id": uuid.uuid4().hex, "chat_id": chat_id, "role": role, "content": content,
                   "tools": tools or [], "created_at": now}
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages (id, chat_id, role, content, tools_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message["id"], chat_id, role, content, json.dumps(message["tools"], default=str), now),
            )
            self._conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        return message

    def history(self, chat_id: str) -> list[dict]:
        chat = self.get_chat(chat_id)
        if chat is None:
            return []
        return [
            {"role": message["role"], "content": message["content"]}
            for message in chat["messages"]
            if message["content"].strip()
        ]


_store: Optional[ChatStore] = None
_store_lock = threading.Lock()


def get_chat_store() -> ChatStore:
    """Process-wide store at the current db_path(); created once even under concurrent requests."""
    global _store
    with _store_lock:
        if _store is None or _store.path != db_path():
            if _store is not None:
                _store.close()
            _store = ChatStore()
        return _store
