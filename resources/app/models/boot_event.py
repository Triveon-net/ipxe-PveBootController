"""History of boot reports sent by nodes."""

from __future__ import annotations

from core.model import Model


class BootEvent(Model):
    table = "boot_events"
    casts = {"payload": "json"}

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    @classmethod
    def pending(cls) -> list[dict]:
        """Latest boot report per MAC for MACs that have no node yet.

        Returns plain dicts (not models) since the result is an aggregate.
        """
        return cls.db().query(
            """
            SELECT b.* FROM boot_events b
            JOIN (
                SELECT mac_address, MAX(id) AS max_id
                FROM boot_events GROUP BY mac_address
            ) latest ON latest.max_id = b.id
            WHERE b.mac_address NOT IN (SELECT mac_address FROM nodes)
              AND b.mac_address NOT IN (SELECT mac_address FROM rejected_nodes)
            ORDER BY b.id DESC
            """
        )

    @classmethod
    def latest_for_mac(cls, mac_address: str) -> "BootEvent | None":
        return (
            cls.where("mac_address", mac_address).order_by("id", "desc").first()
        )

    @classmethod
    def purge(cls, mac_address: str) -> int:
        """Delete every boot report for a MAC. Returns the number removed."""
        return cls.where("mac_address", mac_address).delete()

    def node(self):
        from resources.app.models.node import Node

        node_id = self._attributes.get("node_id")
        return Node.find(node_id) if node_id else None
