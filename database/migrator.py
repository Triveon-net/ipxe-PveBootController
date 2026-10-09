"""Tiny file-based migration runner.

Each module in `database/migrations/` named ``NNNN_description.py`` exposes an
``up(connection)`` function (and optionally ``down(connection)``). Applied
migrations are recorded in the ``migrations`` table.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from database.database import Database

_NAME_RE = re.compile(r"^(\d+)_([A-Za-z0-9_]+)\.py$")


@dataclass
class Migration:
    name: str
    path: Path
    up: Callable
    down: Callable | None


class Migrator:
    def __init__(self, db: Database, migrations_path: Path) -> None:
        self.db = db
        self.migrations_path = migrations_path

    def _ensure_table(self) -> None:
        self.db.run(
            """
            CREATE TABLE IF NOT EXISTS migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                batch INTEGER NOT NULL DEFAULT 1,
                applied_at TEXT NOT NULL
            )
            """
        )

    def _discover(self) -> list[Migration]:
        found: list[Migration] = []
        if not self.migrations_path.is_dir():
            return found
        for path in sorted(self.migrations_path.glob("*.py")):
            match = _NAME_RE.match(path.name)
            if not match:
                continue
            module = self._load_module(path)
            up = getattr(module, "up", None)
            if not callable(up):
                continue
            found.append(
                Migration(
                    name=path.stem,
                    path=path,
                    up=up,
                    down=getattr(module, "down", None),
                )
            )
        return found

    @staticmethod
    def _load_module(path: Path):
        spec = importlib.util.spec_from_file_location(f"migrations.{path.stem}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def applied(self) -> set[str]:
        self._ensure_table()
        rows = self.db.query("SELECT name FROM migrations")
        return {row["name"] for row in rows}

    def pending(self) -> list[Migration]:
        applied = self.applied()
        return [m for m in self._discover() if m.name not in applied]

    def run(self) -> list[str]:
        from core.support import now_iso

        applied: list[str] = []
        for migration in self.pending():
            conn = self.db.connection()
            conn.execute("BEGIN")
            try:
                migration.up(conn)
                conn.execute(
                    "INSERT INTO migrations (name, batch, applied_at) VALUES (?, 1, ?)",
                    (migration.name, now_iso()),
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
            applied.append(migration.name)
        return applied

    def rollback(self) -> list[str]:
        rows = self.db.query(
            "SELECT name FROM migrations ORDER BY id DESC LIMIT 1"
        )
        if not rows:
            return []
        name = rows[0]["name"]
        migration = next((m for m in self._discover() if m.name == name), None)
        rolled: list[str] = []
        conn = self.db.connection()
        conn.execute("BEGIN")
        try:
            if migration and migration.down:
                migration.down(conn)
            conn.execute("DELETE FROM migrations WHERE name = ?", (name,))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        rolled.append(name)
        return rolled
