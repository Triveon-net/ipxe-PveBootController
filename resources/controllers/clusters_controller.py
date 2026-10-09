"""Clusters controller (Proxmox cluster + Corosync configuration)."""

from __future__ import annotations

from core.exceptions import NotFound, ValidationException
from core.response import text_response
from core.support import slugify
from resources.app.models.cluster import Cluster
from resources.app.services.corosync import CorosyncConfigGenerator
from resources.controllers.controller import Controller, is_htmx

RULES = {
    "name": "required|hostname",
    "description": "nullable|string|max:255",
    "quorum_expected": "required|integer|min:1|max:64",
    "corosync_config": "nullable|json",
}


class ClustersController(Controller):
    def index(self, request):
        clusters = Cluster.query().order_by("name").get()
        return self.render_page(
            "pages/clusters/index.html",
            {"clusters": clusters, "active": "clusters"},
            request,
        )

    def show(self, request, cluster_id: int):
        cluster = self._find(cluster_id)
        nodes = cluster.nodes()
        generator = CorosyncConfigGenerator()
        node_ids = generator.assign_node_ids(nodes)
        config = generator.sync(cluster)
        return self.render_page(
            "pages/clusters/show.html",
            {
                "cluster": cluster,
                "nodes": nodes,
                "node_ids": node_ids,
                "corosync_conf": config,
                "active": "clusters",
            },
            request,
        )

    def corosync(self, request, cluster_id: int):
        cluster = self._find(cluster_id)
        config = CorosyncConfigGenerator().sync(cluster)
        response = text_response(config)
        if request.args.get("download"):
            filename = f"{slugify(cluster.name)}-corosync.conf"
            response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    def store(self, request):
        try:
            data = self.validate(request.form, RULES)
        except ValidationException:
            self.flash("Please fix the highlighted errors.", "error")
            return self._done(request, "/clusters")

        if Cluster.where("name", data["name"]).exists():
            self.flash("A cluster with that name already exists.", "error")
            return self._done(request, "/clusters")

        Cluster.create(
            {
                "name": data["name"],
                "description": data.get("description") or "",
                "quorum_expected": data["quorum_expected"],
                "corosync_config": data.get("corosync_config") or {},
            }
        )
        self.flash(f"Cluster '{data['name']}' created.", "success")
        return self._done(request, "/clusters")

    def destroy(self, request, cluster_id: int):
        cluster = self._find(cluster_id)
        name = cluster.name
        cluster.delete()
        self.flash(f"Cluster '{name}' deleted.", "success")
        return self._done(request, "/clusters")

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _find(cluster_id: int) -> Cluster:
        cluster = Cluster.find(cluster_id)
        if cluster is None:
            raise NotFound("Cluster not found.")
        return cluster

    def _done(self, request, url: str):
        if is_htmx(request):
            return self.htmx_redirect(url)
        return self.redirect(url)
