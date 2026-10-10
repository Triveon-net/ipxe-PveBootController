"""Raw config syntax: cluster corosync text + split node corosync overrides.

* `clusters.corosync_config` becomes the user's **raw** corosync.conf text
  (empty = auto-generate). The old JSON options (config_version/topology/totem
  overrides) move to the internal `corosync_state`.
* `nodes.storage_config` / `nodes.network_config` become free text (PVE
  storage.cfg / interfaces syntax). The Corosync ring address and quorum votes
  that used to live inside the network JSON get their own columns.
"""

from __future__ import annotations


def up(conn) -> None:
    # Cluster: internal generator state vs. the operator's raw config.
    conn.execute(
        "ALTER TABLE clusters ADD COLUMN corosync_state TEXT NOT NULL DEFAULT '{}'"
    )
    conn.execute("UPDATE clusters SET corosync_state = corosync_config")
    conn.execute("UPDATE clusters SET corosync_config = ''")

    # Node: corosync overrides formerly embedded in the network JSON.
    conn.execute(
        "ALTER TABLE nodes ADD COLUMN corosync_ring0_addr TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "ALTER TABLE nodes ADD COLUMN quorum_votes INTEGER NOT NULL DEFAULT 1"
    )


def down(conn) -> None:
    conn.execute("UPDATE clusters SET corosync_config = corosync_state")
    for table, column in (
        ("clusters", "corosync_state"),
        ("nodes", "corosync_ring0_addr"),
        ("nodes", "quorum_votes"),
    ):
        try:
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        except Exception:
            pass
