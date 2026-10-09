"""Node boot reporting API.

A booting node POSTs its hardware inventory here. We look the node up by the
MAC address of its provisioning interface, log the event, refresh the stored
hardware data and return the configuration the node should apply.
"""

from __future__ import annotations

from core.exceptions import ValidationException
from core.support import now_iso
from resources.app.models.boot_event import BootEvent
from resources.app.models.node import Node
from resources.controllers.controller import Controller

REPORT_RULES = {
    "mac_address": "required|mac",
    "cpu_model": "nullable|string|max:255",
    "cpu_count": "nullable|integer|min:0|max:4096",
    "ram_gb": "nullable|integer|min:0|max:1048576",
    "ip_address": "nullable|ipv4",
}


class BootApiController(Controller):
    def report(self, request):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = request.form.to_dict() if request.form else {}

        try:
            data = self.validate(payload, REPORT_RULES)
        except ValidationException as exc:
            return self.json({"error": exc.message, "errors": exc.errors}, status=422)

        mac = data["mac_address"]
        node = Node.find_by_mac(mac)

        BootEvent.create(
            {
                "node_id": node.id if node else None,
                "mac_address": mac,
                "ip_address": data.get("ip_address") or "",
                "cpu_model": data.get("cpu_model") or "",
                "cpu_count": data.get("cpu_count") or 0,
                "ram_gb": data.get("ram_gb") or 0,
                "payload": payload,
                "created_at": now_iso(),
            }
        )

        if node is None:
            # Unknown MAC: recorded for the operator to map in the panel.
            return self.json(
                {
                    "registered": False,
                    "message": "Unknown MAC address. Create a node with this MAC in the panel.",
                    "mac_address": mac,
                },
                status=202,
            )

        node.update(
            {
                "cpu_model": data.get("cpu_model") or node.get("cpu_model", ""),
                "cpu_count": data.get("cpu_count") or node.get("cpu_count", 0),
                "ram_gb": data.get("ram_gb") or node.get("ram_gb", 0),
                "last_report_at": now_iso(),
                "last_boot_at": now_iso(),
            }
        )

        return self.json(
            {"registered": True, "node": self._node_summary(node), "config": self._config(node)}
        )

    def fetch(self, request, mac: str):
        from core.security import ValidationError, Validator

        try:
            normalised = Validator.mac(mac)
        except ValidationError as exc:
            return self.json({"error": str(exc)}, status=422)

        node = Node.find_by_mac(normalised)
        if node is None:
            return self.json({"error": "Unknown MAC address."}, status=404)
        return self.json({"node": self._node_summary(node), "config": self._config(node)})

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _node_summary(node: Node) -> dict:
        return {
            "id": node.id,
            "name": node.name,
            "mac_address": node.mac_address,
            "ip_address": node.ip_address,
            "status": node.status,
        }

    @staticmethod
    def _config(node: Node) -> dict:
        cluster = node.cluster
        cluster_payload = None
        corosync_conf = None
        if cluster is not None:
            from resources.app.services.corosync import CorosyncConfigGenerator

            cluster_payload = {
                "name": cluster.name,
                "quorum_expected": cluster.quorum_expected,
                "corosync": cluster.get("corosync_config", {}),
            }
            # Generated live from the cluster's current nodes (auto node IDs +
            # config_version); the node can write this straight to corosync.conf.
            corosync_conf = CorosyncConfigGenerator().sync(cluster)

        return {
            "name": node.name,
            "ip_address": node.ip_address,
            "netmask": node.netmask,
            "gateway": node.gateway,
            "vlan": node.vlan,
            "dns": node.dns,
            "bridge": node.bridge,
            "network": node.get("network_config", {}),
            "storage": node.get("storage_config", []),
            "vms": node.get("vm_config", []),
            "cluster": cluster_payload,
            "corosync_node_id": node.corosync_node_id,
            "corosync_conf": corosync_conf,
        }
