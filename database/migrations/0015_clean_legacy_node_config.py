"""Clean legacy JSON defaults out of the node network/storage config.

Before the raw-syntax switch, `nodes.network_config` / `storage_config` were JSON
objects with defaults `{}` / `[]`. Those were being appended verbatim to
`/etc/network/interfaces` (a literal `{}`) and `/etc/pve/storage.cfg`. Any
Corosync overrides that used to live in the network JSON are moved to the
dedicated columns added in migration 0013.
"""

from __future__ import annotations

import json


def _parse(value) -> object | None:
    """Return the parsed JSON container, or None if the value is not JSON."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def up(conn) -> None:
    rows = conn.execute(
        "SELECT id, network_config, storage_config, corosync_ring0_addr, quorum_votes "
        "FROM nodes"
    ).fetchall()
    for row in rows:
        network = _parse(row["network_config"])
        if network is not None:
            sets = ["network_config = ''"]
            params: list = []
            if isinstance(network, dict):
                ring0 = network.get("corosync_ring0_addr") or network.get("ring0_addr")
                votes = network.get("quorum_votes")
                if ring0 and not row["corosync_ring0_addr"]:
                    sets.append("corosync_ring0_addr = ?")
                    params.append(str(ring0))
                if votes not in (None, "") and row["quorum_votes"] in (None, 1):
                    try:
                        sets.append("quorum_votes = ?")
                        params.append(int(votes))
                    except (TypeError, ValueError):
                        pass
            conn.execute(
                f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?",
                params + [row["id"]],
            )

        if _parse(row["storage_config"]) is not None:
            conn.execute(
                "UPDATE nodes SET storage_config = '' WHERE id = ?", (row["id"],)
            )


def down(conn) -> None:
    # The legacy JSON defaults were meaningless; nothing to restore.
    pass
