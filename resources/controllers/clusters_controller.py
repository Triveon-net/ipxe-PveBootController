"""Clusters controller (Proxmox cluster + Corosync configuration)."""

from __future__ import annotations

from core.exceptions import NotFound, ValidationException
from core.response import text_response
from core.support import slugify
from resources.app.models.cluster import Cluster
from resources.app.models.storage import Storage
from resources.app.services.corosync import CorosyncConfigGenerator
from resources.controllers.controller import Controller

RULES = {
    "name": "required|cluster_name",
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
        # Make sure the cluster secret material exists so it is ready to hand to
        # the nodes (authkey + per-node SSH keys).
        from resources.app.services.cluster_keys import ClusterKeys

        ClusterKeys.ensure_authkey(cluster)
        ClusterKeys.bundle(cluster)
        return self.render_page(
            "pages/clusters/show.html",
            {
                "cluster": cluster,
                "nodes": nodes,
                "node_ids": node_ids,
                "corosync_conf": config,
                "storages": Storage.query().order_by("storage_id").get(),
                "assigned_storage_ids": Storage.ids_for_cluster(cluster.id),
                "secrets": ClusterKeys.status(cluster),
                "active": "clusters",
            },
            request,
        )

    def save_storages(self, request, cluster_id: int):
        cluster = self._find(cluster_id)
        Storage.sync_for_cluster(cluster.id, request.form.getlist("storage_ids"))
        self._push_all()
        self.flash("Cluster storage updated.", "success")
        return self._done(request, f"/clusters/{cluster.id}")

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
        self._push_all()
        self.flash(f"Cluster '{name}' deleted.", "success")
        return self._done(request, "/clusters")

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _push_all() -> None:
        """Push fresh configs to every connected agent (cluster/storage change)."""
        from core.websocket import get_hub

        get_hub().notify_all()

    @staticmethod
    def _find(cluster_id: int) -> Cluster:
        cluster = Cluster.find(cluster_id)
        if cluster is None:
            raise NotFound("Cluster not found.")
        return cluster
