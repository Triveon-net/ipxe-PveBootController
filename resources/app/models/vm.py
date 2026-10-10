"""VM configs synced from nodes.

The identity is ``(node_id, vmid)``: VMIDs are only unique per node, so two
standalone nodes can both host a VM 100 without conflicting. `cluster_id` is a
snapshot of the node's cluster at sync time (NULL when standalone).
"""

from __future__ import annotations

from typing import Any

from core.model import Model
from core.support import now_iso


def _as_vmid(value: Any) -> int | None:
    try:
        vmid = int(value)
    except (TypeError, ValueError):
        return None
    return vmid if 1 <= vmid <= 999999999 else None


class Vm(Model):
    table = "vms"

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    @classmethod
    def for_node(cls, node_id: int) -> list[Vm]:
        return cls.where("node_id", node_id).order_by("vmid").get()

    @classmethod
    def counts_by_node(cls) -> dict[int, int]:
        rows = cls.db().query("SELECT node_id, COUNT(*) AS c FROM vms GROUP BY node_id")
        return {int(row["node_id"]): int(row["c"]) for row in rows}

    # -- writes -------------------------------------------------------------
    @classmethod
    def upsert_for_node(cls, node_id: int, cluster_id: int | None, items: list[dict]) -> int:
        """Add/update the reported VMs without removing any (boot grace window)."""
        db = cls.db()
        now = now_iso()
        seen: set[int] = set()
        stored = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            vmid = _as_vmid(item.get("vmid"))
            if vmid is None or vmid in seen:
                continue
            seen.add(vmid)
            db.run(
                "INSERT INTO vms (node_id, cluster_id, vmid, name, config, synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (node_id, vmid) DO UPDATE SET "
                "cluster_id = excluded.cluster_id, name = excluded.name, "
                "config = excluded.config, synced_at = excluded.synced_at",
                (
                    node_id,
                    cluster_id,
                    vmid,
                    str(item.get("name") or "")[:255],
                    str(item.get("config") or "")[:65535],
                    now,
                ),
            )
            stored += 1
        return stored

    @classmethod
    def replace_for_node(cls, node_id: int, cluster_id: int | None, items: list[dict]) -> int:
        """Replace all VMs of a node with the reported set. Returns the count."""
        db = cls.db()
        db.run("DELETE FROM vms WHERE node_id = ?", (node_id,))
        now = now_iso()
        seen: set[int] = set()
        stored = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            vmid = _as_vmid(item.get("vmid"))
            if vmid is None or vmid in seen:
                continue
            seen.add(vmid)
            db.run(
                "INSERT INTO vms (node_id, cluster_id, vmid, name, config, synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    node_id,
                    cluster_id,
                    vmid,
                    str(item.get("name") or "")[:255],
                    str(item.get("config") or "")[:65535],
                    now,
                ),
            )
            stored += 1
        return stored

    # -- paged reads --------------------------------------------------------
    @classmethod
    def page_for_node(
        cls, node_id: int, search: str, limit: int, offset: int
    ) -> tuple[int, list[Vm]]:
        where = "node_id = ?"
        params: list[Any] = [node_id]
        if search:
            where += " AND (CAST(vmid AS TEXT) LIKE ? OR name LIKE ?)"
            like = f"%{search}%"
            params += [like, like]
        total = int(cls.db().scalar(f"SELECT COUNT(*) FROM vms WHERE {where}", params, 0))
        rows = cls.db().query(
            f"SELECT * FROM vms WHERE {where} ORDER BY vmid LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return total, [cls.hydrate(row) for row in rows]
