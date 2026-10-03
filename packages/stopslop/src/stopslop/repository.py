"""Persistence boundary shared by runtime, administration and the terminal UI.

Implement Repository to replace SQLite without changing enforcement or UI code.
Transactions must serialize quota admission, including across processes.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Protocol, ContextManager


class Repository(Protocol):
    def transaction(self) -> ContextManager: ...
    def load_state(self) -> dict: ...
    def save_state(self, value: dict) -> None: ...
    def save_session(self, session_id: str, value: dict) -> None: ...
    def sessions(self) -> list[dict]: ...
    def append(self, kind: str, time: float, session_id: str, value: dict) -> int: ...
    def records(self, kind: str, limit: int = 100, offset: int = 0) -> list[dict]: ...
    def count(self, kind: str) -> int: ...
    def record(self, kind: str, record_id: int) -> dict | None: ...
    def dynamic_policy(self) -> dict: ...
    def save_dynamic_policy(self, value: dict) -> None: ...
    def close(self) -> None: ...


class SQLiteRepository:
    def __init__(self, path="stopslop.sqlite3", *, read_only=False):
        self.path = str(path)
        if self.path != ":memory:" and not read_only:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.depth = 0
        location = Path(self.path).resolve().as_uri() + "?mode=ro" if read_only else self.path
        self.db = sqlite3.connect(location, uri=read_only, timeout=30, check_same_thread=False, isolation_level=None)
        if not read_only:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS control_state (
                id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, time REAL NOT NULL,
                session_id TEXT NOT NULL, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_kind_time ON records(kind, time DESC, id DESC);
            CREATE TABLE IF NOT EXISTS dynamic_policy (
                id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            outer = self.depth == 0
            if outer:
                self.db.execute("BEGIN IMMEDIATE")
            self.depth += 1
            try:
                yield self
                if outer:
                    self.db.execute("COMMIT")
            except BaseException:
                if outer:
                    self.db.execute("ROLLBACK")
                raise
            finally:
                self.depth -= 1

    def load_state(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM control_state WHERE id=1").fetchone()
            return json.loads(row[0]) if row else dict(events=[], pending={}, pending_owners={}, blocked_clients=[])

    def save_state(self, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO control_state VALUES (1, ?)", (json.dumps(value),))

    def save_session(self, session_id, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session_id, json.dumps(value)))

    def sessions(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT value FROM sessions")]

    def append(self, kind, time, session_id, value):
        with self.lock:
            cursor = self.db.execute("INSERT INTO records(kind,time,session_id,value) VALUES (?,?,?,?)",
                                     (kind, time, session_id, json.dumps(value)))
            return cursor.lastrowid

    def records(self, kind, limit=100, offset=0):
        with self.lock:
            rows = self.db.execute("SELECT id,time,session_id,value FROM records WHERE kind=? "
                                   "ORDER BY time DESC,id DESC LIMIT ? OFFSET ?", (kind, limit, offset))
            return [dict(json.loads(value), id=record_id, time=time, session_id=session_id)
                    for record_id, time, session_id, value in rows]

    def count(self, kind):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM records WHERE kind=?", (kind,)).fetchone()[0]

    def record(self, kind, record_id):
        with self.lock:
            row = self.db.execute("SELECT time,session_id,value FROM records WHERE kind=? AND id=?",
                                  (kind, record_id)).fetchone()
            return dict(json.loads(row[2]), id=record_id, time=row[0], session_id=row[1]) if row else None

    def dynamic_policy(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM dynamic_policy WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {"version": 1, "rules": []}

    def save_dynamic_policy(self, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO dynamic_policy VALUES (1, ?)", (json.dumps(value),))

    def close(self):
        with self.lock:
            self.db.close()
