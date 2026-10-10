"""Add the node's bridge port and the NIC reported by the agent.

`bridge_port` is the physical NIC that `vmbrX` bridges to (the agent uses it for
`bridge-ports`, falling back to the NIC it detected). `detected_nic` is the NIC
the agent reports it gets its connection on, so the operator can use it as the
default.
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute("ALTER TABLE nodes ADD COLUMN bridge_port TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE nodes ADD COLUMN detected_nic TEXT NOT NULL DEFAULT ''")


def down(conn) -> None:
    for column in ("bridge_port", "detected_nic"):
        try:
            conn.execute(f'ALTER TABLE "nodes" DROP COLUMN "{column}"')
        except Exception:
            pass
