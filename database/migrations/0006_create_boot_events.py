"""Create the boot_events table.

Every boot report a node sends is logged here for auditing and history.
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE boot_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id     INTEGER,
            mac_address TEXT NOT NULL,
            ip_address  TEXT NOT NULL DEFAULT '',
            cpu_model   TEXT NOT NULL DEFAULT '',
            cpu_count   INTEGER NOT NULL DEFAULT 0,
            ram_gb      INTEGER NOT NULL DEFAULT 0,
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (node_id) REFERENCES nodes (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_boot_events_mac ON boot_events (mac_address)")
    conn.execute("CREATE INDEX idx_boot_events_node ON boot_events (node_id)")


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS boot_events")
