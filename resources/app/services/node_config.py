"""Builds the payload a booting node receives (HTTP API and WebSocket hub).

Kept in one place so the pushed WebSocket config and the HTTP `POST
/api/boot/report` response can never drift apart.
"""

from __future__ import annotations

from resources.app.models.node import Node


def _raw_config(value) -> str:
    """Free-text config, ignoring empty or legacy-JSON values ("{}"/"[]")."""
    text = value if isinstance(value, str) else ""
    text = text.strip()
    return "" if not text or text[0] in "{[" else text


class NodeConfig:
    @staticmethod
    def summary(node: Node) -> dict:
        return {
            "id": node.id,
            "name": node.name,
            "mac_address": node.mac_address,
            "ip_address": node.ip_address,
            "status": node.effective_status(),
        }

    @staticmethod
    def for_node(node: Node) -> dict:
        from resources.app.models.vm import Vm
        from resources.app.services.cluster_keys import ClusterKeys

        cluster = node.cluster
        cluster_payload = None
        corosync_conf = None
        corosync_authkey = ""
        root_ca: dict = {"key": "", "pem": ""}
        ssh = {"private": "", "public": "", "authorized_keys": "", "known_hosts": ""}
        cluster_hosts: list[dict] = []

        if cluster is not None:
            from resources.app.services.corosync import CorosyncConfigGenerator

            cluster_payload = {
                "name": cluster.name,
                "quorum_expected": cluster.quorum_expected,
                "corosync": cluster.get("corosync_config", {}),
            }
            # Generated live from the cluster's current nodes (auto node IDs +
            # config_version); the node can write this straight to corosync.conf.
            corosync_conf = CorosyncConfigGenerator().sync(cluster)

            # Cluster-wide secrets: identical authkey everywhere, plus the SSH
            # trust material every node needs to reach the others.
            node = ClusterKeys.ensure_node_keys(node)
            corosync_authkey = ClusterKeys.ensure_authkey(cluster)
            root_ca = ClusterKeys.ensure_root_ca(cluster)
            keys = ClusterKeys.bundle(cluster)
            ssh = {
                "private": node.get("ssh_private_key", ""),
                "public": node.get("ssh_public_key", ""),
                "authorized_keys": keys["authorized_keys"],
                "known_hosts": keys["known_hosts"],
            }
            # Name resolution for every cluster node (/etc/hosts replication).
            cluster_hosts = [
                {"ip": n.ip_address, "name": n.name}
                for n in cluster.nodes()
                if n.get("ip_address") and n.get("name")
            ]

        # VM configs stored for this node (restored by the agent when missing).
        vm_configs = [
            {"vmid": vm.vmid, "name": vm.name, "config": vm.config}
            for vm in Vm.for_node(node.id)
        ]

        return {
            "name": node.name,
            "ip_address": node.ip_address,
            "netmask": node.netmask,
            "gateway": node.gateway,
            "vlan": node.vlan,
            "dns": node.dns,
            "bridge": node.bridge,
            "bridge_port": node.get("bridge_port", ""),
            "network": _raw_config(node.get("network_config", "")),
            "storage": _raw_config(node.get("storage_config", "")),
            "vms": node.get("vm_config", []),
            "storages": [
                {
                    "id": s.storage_id,
                    "kind": s.kind,
                    "server": s.server,
                    "export": s.export,
                    "reachable": bool(s.reachable),
                }
                for s in node.effective_storages()
            ],
            "cluster": cluster_payload,
            "corosync_node_id": node.corosync_node_id,
            "corosync_conf": corosync_conf,
            "corosync_authkey": corosync_authkey,
            "cluster_ca": root_ca,
            "ssh": ssh,
            "cluster_hosts": cluster_hosts,
            "vm_configs": vm_configs,
        }
