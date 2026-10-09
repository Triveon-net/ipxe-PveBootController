"""Database package: connection manager + a lazily created singleton."""

from __future__ import annotations

from database.database import Database, quote_identifier

__all__ = ["Database", "quote_identifier", "get_db", "set_db"]

_instance: Database | None = None


def get_db() -> Database:
    """Return the process-wide database instance (created on first use)."""
    global _instance
    if _instance is None:
        from config import get_config

        _instance = Database(get_config().database_path)
    return _instance


def set_db(database: Database) -> None:
    global _instance
    _instance = database
