"""History of boot reports sent by nodes."""

from __future__ import annotations

from core.model import Model


class BootEvent(Model):
    table = "boot_events"
    casts = {"payload": "json"}

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    def node(self):
        from resources.app.models.node import Node

        node_id = self._attributes.get("node_id")
        return Node.find(node_id) if node_id else None
