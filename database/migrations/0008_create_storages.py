"""Create the storages table (NFS shares and future storage types)."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE storages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            storage_id      TEXT NOT NULL UNIQUE,
            kind            TEXT NOT NULL DEFAULT 'nfs',
            server          TEXT NOT NULL,
            export          TEXT NOT NULL,
            options         TEXT NOT NULL DEFAULT '{}',
            reachable       INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS storages")
