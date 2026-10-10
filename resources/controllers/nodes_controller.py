"""Nodes controller: list, create, edit and delete node configurations."""

from __future__ import annotations

import json

from core.exceptions import NotFound, ValidationException
from core.security import ValidationError, Validator
from resources.app.models.boot_event import BootEvent
from resources.app.models.cluster import Cluster
from resources.app.models.node import Node
from resources.app.models.rejected_node import RejectedNode
from resources.app.models.storage import Storage
from resources.controllers.controller import Controller

RULES = {
    "name": "required|hostname",
    "mac_address": "required|mac",
    "ip_address": "required|ipv4",
    "netmask": "nullable|netmask",
    "gateway": "nullable|ipv4",
    "vlan": "nullable|vlan",
    "dns": "nullable|string|max:255",
    "bridge": "nullable|string|max:32",
    "bridge_port": "nullable|string|max:32",
    "cluster_id": "nullable|integer",
    "corosync_node_id": "nullable|integer",
    "corosync_ring0_addr": "nullable|ipv4",
    "quorum_votes": "nullable|integer|min:1|max:32",
    "storage_config": "nullable|string|max:16384",
    "vm_config": "nullable|json_list",
    "network_config": "nullable|string|max:16384",
}


class NodesController(Controller):
    # -- read ---------------------------------------------------------------
    def index(self, request):
        nodes = Node.query().order_by("name").get()
        return self.render_page(
            "pages/nodes/index.html",
            {
                "nodes": nodes,
                "pending_count": len(BootEvent.pending()),
                "online_macs": self._online_macs(),
                "active": "nodes",
            },
            request,
        )

    def pending(self, request):
        return self.render_page(
            "pages/nodes/pending.html",
            {
                "pending": BootEvent.pending(),
                "rejected": RejectedNode.query().order_by("id", "desc").get(),
                "online_macs": self._online_macs(),
                "active": "nodes",
            },
            request,
        )

    def reject(self, request, mac: str):
        mac = self._valid_mac(mac)
        RejectedNode.reject(mac, request.form.get("reason", "")[:255])
        self.flash(f"Node {mac} rejected.", "success")
        return self._done(request, "/nodes/pending")

    def unreject(self, request, mac: str):
        mac = self._valid_mac(mac)
        RejectedNode.restore(mac)
        self.flash(f"Node {mac} is pending again.", "success")
        return self._done(request, "/nodes/pending")

    def destroy_rejected(self, request, mac: str):
        mac = self._valid_mac(mac)
        RejectedNode.restore(mac)
        removed = BootEvent.purge(mac)
        self.flash(
            f"Removed {mac} from the system ({removed} boot report(s) deleted).",
            "success",
        )
        return self._done(request, "/nodes/pending")

    def show(self, request, node_id: int):
        node = self._find(node_id)
        events = (
            BootEvent.where("node_id", node.id).order_by("id", "desc").limit(20).get()
        )
        return self.render_page(
            "pages/nodes/show.html",
            {
                "node": node,
                "events": events,
                "node_storages": node.storages(),
                "cluster_storages": node.cluster.storages() if node.cluster else [],
                "active": "nodes",
            },
            request,
        )

    def create(self, request):
        values = self._default_values()
        pending_report = None
        pending_nic = ""

        mac = (request.args.get("mac") or "").strip()
        if mac:
            try:
                mac = Validator.mac(mac)
            except ValidationError:
                mac = ""
            if mac:
                values["mac_address"] = mac
                latest = BootEvent.latest_for_mac(mac)
                if latest is not None:
                    pending_report = latest
                    # Pre-fill the bridge port with the NIC the agent reported.
                    payload = latest.get("payload")
                    pending_nic = payload.get("nic") if isinstance(payload, dict) else ""
                    if pending_nic:
                        values["bridge_port"] = pending_nic

        return self.render_page(
            "pages/nodes/create.html",
            {
                "values": values,
                "errors": None,
                "node": None,
                "clusters": self._clusters(),
                "storages": self._storages(),
                "selected_storage_ids": [],
                "pending_report": pending_report,
                "pending_nic": pending_nic,
                "active": "nodes",
            },
            request,
        )

    def edit(self, request, node_id: int):
        node = self._find(node_id)
        return self.render_page(
            "pages/nodes/edit.html",
            {
                "values": self._node_values(node),
                "errors": None,
                "node": node,
                "clusters": self._clusters(),
                "storages": self._storages(),
                "selected_storage_ids": Storage.ids_for_node(node.id),
                "active": "nodes",
            },
            request,
        )

    # -- write --------------------------------------------------------------
    def store(self, request):
        try:
            data = self.validate(request.form, RULES)
        except ValidationException as exc:
            return self._form_error(request, exc.errors, request.form, None)

        errors = self._uniqueness(None, data["name"], data["mac_address"])
        if errors:
            return self._form_error(request, errors, request.form, None)

        node = Node.create(self._normalise(data))
        Storage.sync_for_node(node.id, request.form.getlist("storage_ids"))
        RejectedNode.restore(node.mac_address)
        self._push_related(node)
        self.flash(f"Node '{node.name}' created.", "success")
        return self._done(request, "/nodes")

    def update(self, request, node_id: int):
        node = self._find(node_id)
        try:
            data = self.validate(request.form, RULES)
        except ValidationException as exc:
            return self._form_error(request, exc.errors, request.form, node)

        errors = self._uniqueness(node.id, data["name"], data["mac_address"])
        if errors:
            return self._form_error(request, errors, request.form, node)

        previous_cluster_id = node.cluster_id
        node.update(self._normalise(data))
        Storage.sync_for_node(node.id, request.form.getlist("storage_ids"))
        # A node joining/leaving/changing a cluster changes every peer's Corosync
        # topology, so notify the old and the new cluster as well.
        self._push_related(node, previous_cluster_id)
        self.flash(f"Node '{node.name}' updated.", "success")
        return self._done(request, "/nodes")

    def destroy(self, request, node_id: int):
        node = self._find(node_id)
        name = node.name
        mac = node.mac_address
        cluster_id = node.cluster_id
        node.delete()
        # The remaining cluster members must drop the removed node from Corosync.
        self._push_macs({mac} | self._cluster_macs(cluster_id))
        self.flash(f"Node '{name}' deleted.", "success")
        return self._done(request, "/nodes")

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _online_macs() -> set[str]:
        """MACs with a live agent WebSocket (empty when the hub is disabled)."""
        from core.websocket import get_hub

        return get_hub().connected_macs()

    @staticmethod
    def _push_config(mac: str) -> None:
        """Push a fresh config to a connected agent (no-op if it is offline)."""
        from core.websocket import get_hub

        get_hub().notify_macs([mac])

    @classmethod
    def _cluster_macs(cls, cluster_id) -> set[str]:
        if not cluster_id:
            return set()
        return {node.mac_address for node in Node.where("cluster_id", cluster_id).get()}

    @classmethod
    def _push_related(cls, node: Node, also_cluster_id=None) -> None:
        """Push a fresh config to the node and **every member** of its cluster(s).

        Adding, moving or removing a node changes the Corosync topology of the
        whole cluster, so all peers must receive the regenerated config.
        """
        macs = {node.mac_address}
        macs |= cls._cluster_macs(node.cluster_id)
        macs |= cls._cluster_macs(also_cluster_id)
        cls._push_macs(macs)

    @staticmethod
    def _push_macs(macs) -> None:
        if not macs:
            return
        from core.websocket import get_hub

        get_hub().notify_macs(list(macs))

    @staticmethod
    def _find(node_id: int) -> Node:
        node = Node.find(node_id)
        if node is None:
            raise NotFound("Node not found.")
        return node

    @staticmethod
    def _valid_mac(mac: str) -> str:
        try:
            return Validator.mac(mac)
        except ValidationError:
            raise NotFound("Invalid MAC address.")

    @staticmethod
    def _clusters():
        return Cluster.query().order_by("name").get()

    @staticmethod
    def _storages():
        return Storage.query().order_by("storage_id").get()

    @staticmethod
    def _uniqueness(node_id: int | None, name: str, mac: str) -> dict[str, list[str]]:
        errors: dict[str, list[str]] = {}
        name_query = Node.where("name", name)
        mac_query = Node.where("mac_address", mac)
        if node_id is not None:
            name_query = name_query.where("id", node_id, "!=")
            mac_query = mac_query.where("id", node_id, "!=")
        if name_query.exists():
            errors.setdefault("name", []).append("A node with that name already exists.")
        if mac_query.exists():
            errors.setdefault("mac_address", []).append(
                "A node with that MAC address already exists."
            )
        return errors

    @staticmethod
    def _normalise(data: dict) -> dict:
        return {
            "name": data["name"],
            "mac_address": data["mac_address"],
            "ip_address": data["ip_address"],
            "netmask": data.get("netmask") or "255.255.255.0",
            "gateway": data.get("gateway") or "",
            "vlan": data.get("vlan"),
            "dns": data.get("dns") or "",
            "bridge": data.get("bridge") or "vmbr0",
            "bridge_port": data.get("bridge_port") or "",
            "cluster_id": data.get("cluster_id"),
            "corosync_node_id": data.get("corosync_node_id"),
            "corosync_ring0_addr": data.get("corosync_ring0_addr") or "",
            "quorum_votes": data.get("quorum_votes") or 1,
            "storage_config": data.get("storage_config") or "",
            "vm_config": data.get("vm_config") or [],
            "network_config": data.get("network_config") or "",
        }

    @staticmethod
    def _default_values() -> dict:
        return {
            "name": "",
            "mac_address": "",
            "ip_address": "",
            "netmask": "255.255.255.0",
            "gateway": "",
            "vlan": "",
            "dns": "",
            "bridge": "vmbr0",
            "bridge_port": "",
            "cluster_id": "",
            "corosync_node_id": "",
            "corosync_ring0_addr": "",
            "quorum_votes": 1,
            "storage_config": "",
            "vm_config": "[]",
            "network_config": "",
        }

    @staticmethod
    def _node_values(node: Node) -> dict:
        return {
            "name": node.name,
            "mac_address": node.mac_address,
            "ip_address": node.ip_address,
            "netmask": node.netmask,
            "gateway": node.gateway,
            "vlan": "" if node.vlan is None else node.vlan,
            "dns": node.dns,
            "bridge": node.bridge,
            "bridge_port": node.get("bridge_port", ""),
            "cluster_id": "" if node.cluster_id is None else node.cluster_id,
            "corosync_node_id": "" if node.corosync_node_id is None else node.corosync_node_id,
            "corosync_ring0_addr": node.get("corosync_ring0_addr", ""),
            "quorum_votes": node.get("quorum_votes", 1),
            "storage_config": node.get("storage_config", ""),
            "vm_config": json.dumps(node.get("vm_config", []), indent=2),
            "network_config": node.get("network_config", ""),
        }

    def _form_error(self, request, errors, form, node):
        values = {key: form.get(key, "") for key in RULES}
        selected: list[int] = []
        for raw in form.getlist("storage_ids"):
            try:
                selected.append(int(raw))
            except (TypeError, ValueError):
                continue
        return self._render_form(errors, values, node, selected)

    def _render_form(self, errors, values, node, selected_storage_ids):
        from core.response import make_response

        body = self.app.view.render(
            "partials/nodes/form.html",
            {
                "errors": errors,
                "values": values,
                "node": node,
                "clusters": self._clusters(),
                "storages": self._storages(),
                "selected_storage_ids": selected_storage_ids,
            },
        )
        return make_response(body, status=422)
