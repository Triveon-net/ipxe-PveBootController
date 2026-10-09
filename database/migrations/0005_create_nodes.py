"""Create the nodes table.

A node is identified primarily by the MAC address of its provisioning NIC.
All hardware-derived values (cpu/ram) are filled in automatically from boot
reports; the rest is operator configuration.
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE nodes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL UNIQUE,
            mac_address         TEXT NOT NULL UNIQUE,
            ip_address          TEXT NOT NULL DEFAULT '',
            netmask             TEXT NOT NULL DEFAULT '255.255.255.0',
            gateway             TEXT NOT NULL DEFAULT '',
            vlan                INTEGER,
            dns                 TEXT NOT NULL DEFAULT '',
            bridge              TEXT NOT NULL DEFAULT 'vmbr0',
            cpu_model           TEXT NOT NULL DEFAULT '',
            cpu_count           INTEGER NOT NULL DEFAULT 0,
            ram_gb              INTEGER NOT NULL DEFAULT 0,
            cluster_id          INTEGER,
            corosync_node_id    INTEGER,
            storage_config      TEXT NOT NULL DEFAULT '[]',
            vm_config           TEXT NOT NULL DEFAULT '[]',
            network_config      TEXT NOT NULL DEFAULT '{}',
            status              TEXT NOT NULL DEFAULT 'pending',
            last_boot_at        TEXT,
            last_report_at      TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            FOREIGN KEY (cluster_id) REFERENCES clusters (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_nodes_cluster ON nodes (cluster_id)")


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS nodes")
