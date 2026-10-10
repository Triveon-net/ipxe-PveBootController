"""Node boot reporting API.

A booting node POSTs its hardware inventory here. We look the node up by the
MAC address of its provisioning interface, log the event, refresh the stored
hardware data and return the configuration the node should apply.
"""

from __future__ import annotations

from core.exceptions import ValidationException
from resources.app.models.node import Node
from resources.app.services.boot_report import BootReport
from resources.app.services.node_config import NodeConfig
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
        result = BootReport.record(mac, BootReport.hardware_from(data))

        if result["status"] == "rejected":
            return self.json(
                {
                    "registered": False,
                    "rejected": True,
                    "message": result.get("message"),
                    "mac_address": mac,
                },
                status=202,
            )
        if result["status"] == "pending":
            return self.json(
                {
                    "registered": False,
                    "message": result.get("message"),
                    "mac_address": mac,
                },
                status=202,
            )
        return self.json(
            {
                "registered": True,
                "node": result["node"],
                "config": result["config"],
            }
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
        return self.json({"node": NodeConfig.summary(node), "config": NodeConfig.for_node(node)})

