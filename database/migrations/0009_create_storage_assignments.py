"""Create pivot tables assigning storages to nodes and clusters."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE node_storage (
            node_id    INTEGER NOT NULL,
            storage_id INTEGER NOT NULL,
            PRIMARY KEY (node_id, storage_id),
            FOREIGN KEY (node_id) REFERENCES nodes (id) ON DELETE CASCADE,
            FOREIGN KEY (storage_id) REFERENCES storages (id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE cluster_storage (
            cluster_id INTEGER NOT NULL,
            storage_id INTEGER NOT NULL,
            PRIMARY KEY (cluster_id, storage_id),
            FOREIGN KEY (cluster_id) REFERENCES clusters (id) ON DELETE CASCADE,
            FOREIGN KEY (storage_id) REFERENCES storages (id) ON DELETE CASCADE
        )
        """
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS node_storage")
    conn.execute("DROP TABLE IF EXISTS cluster_storage")
