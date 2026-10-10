"""Add cluster secret material: Corosync authkey + per-node SSH keys.

These must be identical (authkey) or known cluster-wide (SSH authorized_keys /
known_hosts) for nodes to form a Proxmox cluster, so the panel is the source of
truth and distributes them to every node during provisioning.
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        "ALTER TABLE clusters ADD COLUMN authkey TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "ALTER TABLE nodes ADD COLUMN ssh_private_key TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "ALTER TABLE nodes ADD COLUMN ssh_public_key TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "ALTER TABLE nodes ADD COLUMN ssh_host_private_key TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "ALTER TABLE nodes ADD COLUMN ssh_host_public_key TEXT NOT NULL DEFAULT ''"
    )


def down(conn) -> None:
    for table, column in (
        ("clusters", "authkey"),
        ("nodes", "ssh_private_key"),
        ("nodes", "ssh_public_key"),
        ("nodes", "ssh_host_private_key"),
        ("nodes", "ssh_host_public_key"),
    ):
        try:
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        except Exception:
            pass
