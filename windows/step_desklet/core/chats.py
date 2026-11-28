"""Local conversation archive shared by the desklet, Chats and voice."""

import ctypes
import json
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path

from step_desklet.core.config import get_config_dir, get_conversation_file


class _ActiveChat:
    pass


ACTIVE_CHAT = _ActiveChat()


def boot_marker() -> str:
    if sys.platform == "win32":
        from ctypes import wintypes

        uptime = ctypes.WinDLL("kernel32").GetTickCount64
        uptime.restype = ctypes.c_uint64
        now = wintypes.FILETIME()
        ctypes.WinDLL("kernel32").GetSystemTimeAsFileTime(ctypes.byref(now))
        ticks = (now.dwHighDateTime << 32) | now.dwLowDateTime
        return str((ticks - uptime() * 10_000) // 10_000_000)
    return str(int(time.time() - time.monotonic()))


class ChatStore:
    def __init__(self, path: Path | None = None, *, legacy: Path | None = None, boot: str | None = None):
        self.path = path or get_config_dir() / "chats.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.boot = boot or boot_marker()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, chat INTEGER NOT NULL REFERENCES chats ON DELETE CASCADE,
                    role TEXT NOT NULL, text TEXT NOT NULL, context TEXT,
                    provider TEXT, source TEXT, state TEXT NOT NULL DEFAULT 'done'
                );
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat, id);
                CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.execute("UPDATE messages SET state='cancelled' WHERE state='pending'")
            if self._get(db, "migrated") is None:
                self._import(db, legacy or get_conversation_file())
                self._set(db, "migrated", "1")
            previous = self._get(db, "boot")
            if previous is None or abs(int(previous) - int(self.boot)) > 5:
                db.execute("DELETE FROM prefs WHERE key='active'")
            self._set(db, "boot", self.boot)

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    @staticmethod
    def _get(db, key):
        row = db.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _set(db, key, value):
        db.execute("INSERT OR REPLACE INTO prefs VALUES (?, ?)", (key, str(value)))

    @staticmethod
    def _new(db, title="New chat"):
        return db.execute("INSERT INTO chats(title, updated) VALUES (?, ?)", (title, time.time())).lastrowid

    def _import(self, db, legacy):
        try:
            rows = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(rows, list):
            return
        rows = [(r["role"], r["content"]) for r in rows if isinstance(r, dict)
                and r.get("role") in {"user", "assistant"} and isinstance(r.get("content"), str)]
        if rows:
            chat = self._new(db, "Previous desklet conversation")
            db.executemany("INSERT INTO messages(chat, role, text, context) VALUES (?, ?, ?, ?)",
                           [(chat, role, text, text) for role, text in rows])

    def active(self):
        with self._db() as db:
            value = self._get(db, "active")
            return int(value) if value else None

    def select(self, chat):
        with self._db() as db:
            if chat is None:
                db.execute("DELETE FROM prefs WHERE key='active'")
            elif db.execute("SELECT 1 FROM chats WHERE id=?", (chat,)).fetchone():
                self._set(db, "active", chat)

    def list(self, query=""):
        with self._db() as db:
            return [dict(row) for row in db.execute("""
                SELECT c.* FROM chats c WHERE ?='' OR instr(lower(c.title), lower(?))>0
                OR EXISTS (SELECT 1 FROM messages m WHERE m.chat=c.id AND instr(lower(m.text), lower(?))>0)
                ORDER BY c.updated DESC, c.id DESC
            """, (query, query, query))]

    def messages(self, chat):
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM messages WHERE chat=? ORDER BY id", (chat,))]

    def title(self, chat):
        if chat is None:
            return None
        with self._db() as db:
            row = db.execute("SELECT title FROM chats WHERE id=?", (chat,)).fetchone()
            return row["title"] if row else None

    def history(self, chat, limit):
        rows = [r for r in self.messages(chat) if r["context"] is not None and r["state"] == "done"]
        return [{"role": r["role"], "content": r["context"][:8000]} for r in rows[-limit:]]

    def begin(self, text, provider, source, *, chat_id: int | None | _ActiveChat = ACTIVE_CHAT):
        with self._db() as db:
            chat = self._get(db, "active") if chat_id is ACTIVE_CHAT else chat_id
            if chat is None or not db.execute("SELECT 1 FROM chats WHERE id=?", (chat,)).fetchone():
                chat = self._new(db, " ".join(text.split())[:76])
            self._set(db, "active", chat)
            message = db.execute("""
                INSERT INTO messages(chat, role, text, provider, source, state)
                VALUES (?, 'user', ?, ?, ?, 'pending')
            """, (chat, text, provider, source)).lastrowid
            db.execute("UPDATE chats SET updated=? WHERE id=?", (time.time(), chat))
            return int(chat), message

    def finish(self, chat, message, answer, context, provider, state="done"):
        with self._db() as db:
            changed = db.execute(
                "UPDATE messages SET state=?, context=CASE WHEN ?='done' THEN text ELSE NULL END "
                "WHERE id=? AND chat=? AND state='pending'", (state, state, message, chat),
            )
            if changed.rowcount and answer:
                db.execute("INSERT INTO messages(chat, role, text, context, provider, state) "
                           "VALUES (?, 'assistant', ?, ?, ?, ?)", (chat, answer, context, provider, state))

    def rename(self, chat, title, *, expected=None, first_message=None):
        with self._db() as db:
            return db.execute(
                """UPDATE chats SET title=? WHERE id=? AND (? IS NULL OR title=?)
                AND (? IS NULL OR ?=(SELECT MIN(id) FROM messages WHERE chat=chats.id))""",
                (title.strip()[:120], chat, expected, expected, first_message, first_message),
            ).rowcount != 0

    def delete(self, chat):
        with self._db() as db:
            if self._get(db, "active") == str(chat):
                db.execute("DELETE FROM prefs WHERE key='active'")
            db.execute("DELETE FROM chats WHERE id=?", (chat,))

    def clear_context(self):
        with self._db() as db:
            db.execute("UPDATE messages SET context=NULL WHERE chat=?", (self._get(db, "active"),))

    def branch(self, chat, before):
        with self._db() as db:
            new = self._new(db, "Edited conversation")
            db.execute("""
                INSERT INTO messages(chat, role, text, context, provider, source, state)
                SELECT ?, role, text, context, provider, source, state FROM messages
                WHERE chat=? AND id<? ORDER BY id
            """, (new, chat, before))
            self._set(db, "active", new)
            return new
