"""Single source of truth for a node's boot report.

Both the HTTP endpoint (`POST /api/boot/report`) and the WebSocket hub funnel
through here, so hardware is recorded and the returned state/config is identical
whichever transport the agent uses.
"""

from __future__ import annotations

from core.security import ValidationError, Validator
from core.support import now_iso
from resources.app.models.boot_event import BootEvent
from resources.app.models.node import Node
from resources.app.models.rejected_node import RejectedNode
from resources.app.services.node_config import NodeConfig


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class BootReport:
    @staticmethod
    def normalise_mac(raw: object) -> str:
        """Return a canonical MAC or raise `ValidationError`."""
        return Validator.mac(str(raw or ""))

    @staticmethod
    def hardware_from(payload: dict) -> dict:
        payload = payload or {}
        return {
            "cpu_model": str(payload.get("cpu_model") or "")[:255],
            "cpu_count": max(0, _int(payload.get("cpu_count"))),
            "ram_gb": max(0, _int(payload.get("ram_gb"))),
            "ip_address": str(payload.get("ip_address") or "")[:45],
        }

    @classmethod
    def record(cls, mac: str, hardware: dict) -> dict:
        """Log a boot event and refresh stored hardware. Returns a state dict."""
        mac = cls.normalise_mac(mac)

        if RejectedNode.exists(mac):
            return {"status": "rejected", "message": "This node was rejected by the operator."}

        node = Node.find_by_mac(mac)

        BootEvent.create(
            {
                "node_id": node.id if node else None,
                "mac_address": mac,
                "ip_address": hardware.get("ip_address") or "",
                "cpu_model": hardware.get("cpu_model") or "",
                "cpu_count": hardware.get("cpu_count") or 0,
                "ram_gb": hardware.get("ram_gb") or 0,
                "payload": hardware,
                "created_at": now_iso(),
            }
        )

        if node is None:
            return {
                "status": "pending",
                "message": "Unknown MAC address. Create a node with this MAC in the panel.",
                "mac_address": mac,
            }

        node.update(
            {
                "cpu_model": hardware.get("cpu_model") or node.get("cpu_model", ""),
                "cpu_count": hardware.get("cpu_count") or node.get("cpu_count", 0),
                "ram_gb": hardware.get("ram_gb") or node.get("ram_gb", 0),
                "detected_nic": hardware.get("nic") or node.get("detected_nic", ""),
                "last_report_at": now_iso(),
                "last_boot_at": now_iso(),
            }
        )
        # A node that reports in is at least being configured; it is promoted to
        # "configured" once the agent confirms a clean apply (see the WS hub).
        if (node.get("status") or "pending") == "pending":
            node.update({"status": "configuring"})
        return {"status": "registered", "node": NodeConfig.summary(node), "config": NodeConfig.for_node(node)}

    @classmethod
    def state(cls, mac: str) -> dict:
        """Current state without recording a boot event (for server pushes)."""
        try:
            mac = cls.normalise_mac(mac)
        except ValidationError:
            return {"status": "pending", "message": "Invalid MAC address."}

        if RejectedNode.exists(mac):
            return {"status": "rejected", "message": "This node was rejected by the operator."}

        node = Node.find_by_mac(mac)
        if node is None:
            return {
                "status": "pending",
                "message": "Unknown MAC address. Create a node with this MAC in the panel.",
                "mac_address": mac,
            }
        return {"status": "registered", "node": NodeConfig.summary(node), "config": NodeConfig.for_node(node)}
