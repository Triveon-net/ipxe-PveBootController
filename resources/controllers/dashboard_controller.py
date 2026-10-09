"""Dashboard controller."""

from __future__ import annotations

from core import auth
from resources.app.models.boot_event import BootEvent
from resources.app.models.cluster import Cluster
from resources.app.models.node import Node
from resources.controllers.controller import Controller


class DashboardController(Controller):
    def root(self, request):
        return self.redirect("/dashboard" if auth.check() else "/login")

    def index(self, request):
        pending = BootEvent.pending()
        stats = {
            "nodes": Node.query().count(),
            "configured": Node.where("status", "configured").count(),
            "pending": Node.where("status", "pending").count(),
            "clusters": Cluster.query().count(),
            "unregistered": len(pending),
        }
        recent_events = (
            BootEvent.query().order_by("id", "desc").limit(8).get()
        )
        recent_nodes = Node.query().order_by("id", "desc").limit(6).get()
        return self.render_page(
            "pages/dashboard/index.html",
            {
                "stats": stats,
                "recent_events": recent_events,
                "recent_nodes": recent_nodes,
                "active": "dashboard",
            },
            request,
        )
