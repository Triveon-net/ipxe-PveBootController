"""Create the boot_files table (uploaded kernel/initrd/squashfs files)."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE boot_files (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            original_name TEXT NOT NULL DEFAULT '',
            size          INTEGER NOT NULL DEFAULT 0,
            sha256        TEXT NOT NULL DEFAULT '',
            content_type  TEXT NOT NULL DEFAULT 'application/octet-stream',
            created_at    TEXT NOT NULL
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS boot_files")
