"""Route definitions (web). Automatically loaded by the application kernel.

The `csrf` middleware is applied to every web route; `auth`/`guest` gate
individual areas. Group middleware is applied outer-most first, so `csrf` runs
before `auth`.
"""

from __future__ import annotations


def register(router) -> None:
    with router.group(middleware=("csrf",)):
        # Authentication
        router.get("/login", "AuthController@showLogin").name("login").middleware("guest")
        router.post("/login", "AuthController@login").middleware("guest")
        router.post("/logout", "AuthController@logout").name("logout").middleware("auth")

        # Root
        router.get("/", "DashboardController@root").name("root")

    with router.group(middleware=("csrf", "auth")):
        router.get("/dashboard", "DashboardController@index").name("dashboard")

        # Nodes
        router.get("/nodes", "NodesController@index").name("nodes.index")
        router.get("/nodes/pending", "NodesController@pending").name("nodes.pending")
        router.post("/nodes/pending/<string:mac>/reject", "NodesController@reject").name(
            "nodes.reject"
        )
        router.post("/nodes/pending/<string:mac>/unreject", "NodesController@unreject").name(
            "nodes.unreject"
        )
        router.get("/nodes/create", "NodesController@create").name("nodes.create")
        router.post("/nodes", "NodesController@store").name("nodes.store")
        router.get("/nodes/<int:node_id>", "NodesController@show").name("nodes.show")
        router.get("/nodes/<int:node_id>/edit", "NodesController@edit").name("nodes.edit")
        router.put("/nodes/<int:node_id>", "NodesController@update").name("nodes.update")
        router.post("/nodes/<int:node_id>", "NodesController@update")
        router.post("/nodes/<int:node_id>/delete", "NodesController@destroy").name(
            "nodes.destroy"
        )

        # Clusters
        router.get("/clusters", "ClustersController@index").name("clusters.index")
        router.post("/clusters", "ClustersController@store").name("clusters.store")
        router.get("/clusters/<int:cluster_id>", "ClustersController@show").name(
            "clusters.show"
        )
        router.get("/clusters/<int:cluster_id>/corosync.conf", "ClustersController@corosync").name(
            "clusters.corosync"
        )
        router.post("/clusters/<int:cluster_id>/storage", "ClustersController@save_storages").name(
            "clusters.storage"
        )
        router.post("/clusters/<int:cluster_id>/delete", "ClustersController@destroy").name(
            "clusters.destroy"
        )

        # Storage (NFS shares)
        router.get("/storage", "StorageController@index").name("storage.index")
        router.post("/storage", "StorageController@store").name("storage.store")
        router.get("/storage/exports", "StorageController@exports").name("storage.exports")
        router.post("/storage/<int:storage_id>/check", "StorageController@check").name(
            "storage.check"
        )
        router.post("/storage/<int:storage_id>/delete", "StorageController@destroy").name(
            "storage.destroy"
        )

        # Boot script (iPXE) configuration
        router.get("/boot", "BootController@index").name("boot.index")
        router.post("/boot", "BootController@update").name("boot.update")
        router.post("/boot/preview", "BootController@preview").name("boot.preview")

        # Boot files (upload / delete)
        router.post("/boot/files", "BootFilesController@store").name("bootfiles.store")
        router.post("/boot/files/<int:file_id>/delete", "BootFilesController@destroy").name(
            "bootfiles.destroy"
        )

        # Settings
        router.get("/settings", "SettingsController@index").name("settings.index")
        router.post("/settings", "SettingsController@update").name("settings.update")
