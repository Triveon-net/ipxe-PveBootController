"""VMs tab: a paginated node list, drill-down into a node's VMs."""

from __future__ import annotations

from core.exceptions import NotFound
from core.pagination import Pager, page_args
from resources.app.models.cluster import Cluster
from resources.app.models.node import Node
from resources.app.models.vm import Vm
from resources.controllers.controller import Controller


class VmsController(Controller):
    def index(self, request):
        page, per_page, offset = page_args(request)
        search = (request.args.get("q") or "").strip()
        total, nodes = Node.page(search, per_page, offset)
        return self.render_page(
            "pages/vms/index.html",
            {
                "nodes": nodes,
                "counts": Vm.counts_by_node(),
                "cluster_names": {c.id: c.name for c in Cluster.query().get()},
                "search": search,
                "pager": Pager(page, per_page, total),
                "base": "/vms",
                "active": "vms",
            },
            request,
        )

    def show(self, request, node_id: int):
        node = Node.find(node_id)
        if node is None:
            raise NotFound("Node not found.")
        page, per_page, offset = page_args(request)
        search = (request.args.get("q") or "").strip()
        total, vms = Vm.page_for_node(node.id, search, per_page, offset)
        return self.render_page(
            "pages/vms/show.html",
            {
                "node": node,
                "vms": vms,
                "search": search,
                "pager": Pager(page, per_page, total),
                "base": f"/vms/{node.id}",
                "active": "vms",
            },
            request,
        )
