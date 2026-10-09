"""Create the key/value settings table."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS settings")
