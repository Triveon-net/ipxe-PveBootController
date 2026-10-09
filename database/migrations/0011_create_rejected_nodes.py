"""Create the rejected_nodes table (MACs an operator has dismissed)."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE rejected_nodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            mac_address TEXT NOT NULL UNIQUE,
            reason      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS rejected_nodes")
