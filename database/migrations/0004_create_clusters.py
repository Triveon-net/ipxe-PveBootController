"""Create the clusters table (Proxmox cluster + Corosync settings)."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE clusters (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            description     TEXT NOT NULL DEFAULT '',
            quorum_expected INTEGER NOT NULL DEFAULT 1,
            corosync_config TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS clusters")
