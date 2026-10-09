"""Read-only JSON view of configured nodes."""

from __future__ import annotations

from resources.app.models.node import Node
from resources.controllers.controller import Controller


class NodesApiController(Controller):
    def index(self, request):
        nodes = Node.query().order_by("name").get()
        return self.json(
            {
                "data": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "mac_address": node.mac_address,
                        "ip_address": node.ip_address,
                        "status": node.status,
                        "cpu_model": node.cpu_model,
                        "cpu_count": node.cpu_count,
                        "ram_gb": node.ram_gb,
                        "cluster_id": node.cluster_id,
                        "last_report_at": node.last_report_at,
                    }
                    for node in nodes
                ]
            }
        )
