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
        router.post("/clusters/<int:cluster_id>/delete", "ClustersController@destroy").name(
            "clusters.destroy"
        )

        # Settings
        router.get("/settings", "SettingsController@index").name("settings.index")
        router.post("/settings", "SettingsController@update").name("settings.update")
