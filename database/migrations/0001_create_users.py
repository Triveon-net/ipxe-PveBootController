"""Create the users table."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            first_name    TEXT NOT NULL DEFAULT '',
            last_name     TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            is_active     INTEGER NOT NULL DEFAULT 1,
            last_login_at TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS users")
