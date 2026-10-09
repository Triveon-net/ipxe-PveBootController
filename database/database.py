"""Low-level SQLite access layer.

Security notes
--------------
* Every *value* is passed as a bound parameter. String interpolation of user
  input into SQL is never performed anywhere in the framework.
* `sqlite3` rejects multiple statements per `execute()` call, which blocks
  stacked-query injection in addition to parameter binding.
* Raw *identifiers* (table / column names) cannot be bound, so they must go
  through `quote_identifier()`, which validates them against a strict pattern
  and double-quotes them.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(name: str) -> str:
    """Validate and double-quote a SQL identifier (table/column name).

    Raises `ValueError` for anything that is not a simple identifier, so a
    malicious identifier can never smuggle SQL syntax.
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f'"{name}"'


class Database:
    """Thread-local SQLite connection manager with a tiny query API."""

    def __init__(self, path: str | Path, *, timeout: float = 30.0) -> None:
        self.path = str(path)
        self._timeout = timeout
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []

    # -- connection handling ------------------------------------------------
    def connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self.path,
                timeout=self._timeout,
                isolation_level=None,  # autocommit; transactions are explicit
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def close(self) -> None:
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    # -- query API ----------------------------------------------------------
    def run(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> sqlite3.Cursor:
        """Execute a write statement, returning the cursor."""
        return self.connection().execute(sql, params)

    def query(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute a read statement and return rows as dicts."""
        cursor = self.connection().execute(sql, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def first(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
        default: Any = None,
    ) -> Any:
        row = self.first(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction context manager (commits or rolls back)."""
        conn = self.connection()
        conn.execute("BEGIN")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # -- helpers ------------------------------------------------------------
    def table_exists(self, name: str) -> bool:
        row = self.first(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        )
        return row is not None
