"""Create the per-node VM table.

VM configs are pulled from each node's `/etc/pve/qemu-server/<vmid>.conf`
(and, in a cluster, the node-specific path). VMIDs are only unique *per node*,
so the identity is `(node_id, vmid)` — two standalone nodes may both have a
VM 100 without clashing. `cluster_id` is stored as a snapshot of the node's
cluster at sync time (NULL when the node is standalone).
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE vms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id     INTEGER NOT NULL,
            cluster_id  INTEGER,
            vmid        INTEGER NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            config      TEXT NOT NULL DEFAULT '',
            synced_at   TEXT NOT NULL,
            UNIQUE (node_id, vmid),
            FOREIGN KEY (node_id) REFERENCES nodes (id) ON DELETE CASCADE,
            FOREIGN KEY (cluster_id) REFERENCES clusters (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_vms_node ON vms (node_id)")
    conn.execute("CREATE INDEX idx_vms_cluster ON vms (cluster_id)")
    conn.execute("CREATE INDEX idx_vms_vmid ON vms (vmid)")


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS vms")
