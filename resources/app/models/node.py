"""Proxmox node model."""

from __future__ import annotations

from core.model import Model

STATUS_PENDING = "pending"
STATUS_CONFIGURING = "configuring"
STATUS_CONFIGURED = "configured"
STATUS_ERROR = "error"

STATUS_LABELS = {
    STATUS_PENDING: "Pending",
    STATUS_CONFIGURING: "Configuring",
    STATUS_CONFIGURED: "Configured",
    STATUS_ERROR: "Error",
}


class Node(Model):
    table = "nodes"
    casts = {
        "vm_config": "json",
    }
    hidden = ("ssh_private_key", "ssh_host_private_key")

    @classmethod
    def find_by_mac(cls, mac: str) -> "Node | None":
        return cls.where("mac_address", mac).first()

    @classmethod
    def page(cls, search: str, limit: int, offset: int) -> tuple[int, list["Node"]]:
        where = ""
        params: list = []
        if search:
            where = " WHERE name LIKE ? OR ip_address LIKE ? OR mac_address LIKE ?"
            like = f"%{search}%"
            params = [like, like, like]
        total = int(cls.db().scalar(f"SELECT COUNT(*) FROM nodes{where}", params, 0))
        rows = cls.db().query(
            f"SELECT * FROM nodes{where} ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return total, [cls.hydrate(row) for row in rows]

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self._attributes.get("status", ""), "Unknown")

    @property
    def cluster(self):
        from resources.app.models.cluster import Cluster

        cluster_id = self._attributes.get("cluster_id")
        return Cluster.find(cluster_id) if cluster_id else None

    def storages(self):
        """Storages assigned directly to this node."""
        from resources.app.models.storage import Storage

        return Storage.for_node(self.id)

    def effective_storages(self):
        """Storages for this node: its own plus those of its cluster."""
        from resources.app.models.storage import Storage

        merged: dict[int, Storage] = {s.id: s for s in self.storages()}
        cluster_id = self._attributes.get("cluster_id")
        if cluster_id:
            for storage in Storage.for_cluster(cluster_id):
                merged.setdefault(storage.id, storage)
        return list(merged.values())
