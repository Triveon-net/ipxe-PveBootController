"""Read-only JSON view of configured nodes (paginated, max 50 per page)."""

from __future__ import annotations

from core.pagination import Pager, page_args
from resources.app.models.node import Node
from resources.controllers.controller import Controller


class NodesApiController(Controller):
    def index(self, request):
        page, per_page, offset = page_args(request)
        search = (request.args.get("q") or "").strip()
        total, nodes = Node.page(search, per_page, offset)
        from core.websocket import get_hub

        online = get_hub().connected_macs()
        return self.json(
            {
                "data": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "mac_address": node.mac_address,
                        "ip_address": node.ip_address,
                        "status": node.effective_status(online),
                        "cpu_model": node.cpu_model,
                        "cpu_count": node.cpu_count,
                        "ram_gb": node.ram_gb,
                        "cluster_id": node.cluster_id,
                        "last_report_at": node.last_report_at,
                    }
                    for node in nodes
                ],
                "meta": Pager(page, per_page, total).to_dict(),
            }
        )
