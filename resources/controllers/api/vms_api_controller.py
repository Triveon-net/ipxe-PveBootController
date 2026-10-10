"""JSON API for VMs. Paginated (max 50 per page) so responses stay bounded."""

from __future__ import annotations

from core.exceptions import NotFound
from core.pagination import Pager, page_args
from resources.app.models.node import Node
from resources.app.models.vm import Vm
from resources.controllers.controller import Controller


class VmsApiController(Controller):
    def index(self, request):
        """Paged list of nodes with their VM counts."""
        page, per_page, offset = page_args(request)
        search = (request.args.get("q") or "").strip()
        total, nodes = Node.page(search, per_page, offset)
        counts = Vm.counts_by_node()
        return self.json(
            {
                "data": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "ip_address": node.ip_address,
                        "mac_address": node.mac_address,
                        "cluster_id": node.cluster_id,
                        "status": node.status,
                        "vm_count": counts.get(node.id, 0),
                    }
                    for node in nodes
                ],
                "meta": Pager(page, per_page, total).to_dict(),
            }
        )

    def show(self, request, node_id: int):
        """Paged list of a node's VMs (with their raw configs)."""
        node = Node.find(node_id)
        if node is None:
            raise NotFound("Node not found.")
        page, per_page, offset = page_args(request)
        search = (request.args.get("q") or "").strip()
        total, vms = Vm.page_for_node(node.id, search, per_page, offset)
        return self.json(
            {
                "node": {
                    "id": node.id,
                    "name": node.name,
                    "ip_address": node.ip_address,
                    "cluster_id": node.cluster_id,
                },
                "data": [
                    {
                        "id": vm.id,
                        "vmid": vm.vmid,
                        "name": vm.name,
                        "config": vm.config,
                        "synced_at": vm.synced_at,
                    }
                    for vm in vms
                ],
                "meta": Pager(page, per_page, total).to_dict(),
            }
        )
