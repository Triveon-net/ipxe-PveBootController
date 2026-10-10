"""Proxmox cluster model (holds Corosync configuration)."""

from __future__ import annotations

from core.model import Model


class Cluster(Model):
    table = "clusters"
    casts = {"corosync_config": "json"}
    hidden = ("authkey",)

    def nodes(self) -> list:
        from resources.app.models.node import Node

        return Node.where("cluster_id", self.id).get()

    @property
    def node_count(self) -> int:
        from resources.app.models.node import Node

        return Node.where("cluster_id", self.id).count()

    def storages(self):
        """Storages assigned to this whole cluster."""
        from resources.app.models.storage import Storage

        return Storage.for_cluster(self.id)
