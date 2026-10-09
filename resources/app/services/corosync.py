"""Automatically generate a Corosync configuration for a Proxmox cluster.

The config is derived *live* from the cluster and the nodes currently assigned
to it, matching the syntax Proxmox expects::

    logging { debug: off; to_syslog: yes }

    nodelist {
      node { name: pve02; nodeid: 1; quorum_votes: 1; ring0_addr: 192.168.189.211 }
      ...
    }

    quorum { provider: corosync_votequorum }

    totem {
      cluster_name: AGC-M1
      config_version: 4
      interface { linknumber: 0 }
      ip_version: ipv4-6
      link_mode: passive
      secauth: on
      token_coefficient: 125
      version: 2
    }

Two things happen automatically:

* **Node IDs** — nodes use their explicit ``corosync_node_id`` when set;
  nodes without one (or with a duplicate) get the next free ID, assigned in a
  stable (alphabetical) order.
* **config_version** — whenever the topology changes (nodes added/removed, IDs
  or ring addresses changed) the version is bumped so Corosync reloads.

All values are sanitised before they are written, so a hostile field can never
inject extra Corosync directives.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from core.security import ValidationError, Validator

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

DEFAULTS: dict[str, Any] = {
    "config_version": 1,
    "logging_debug": False,
    "logging_to_syslog": True,
    "quorum_provider": "corosync_votequorum",
    "linknumber": 0,
    "ip_version": "ipv4-6",
    "link_mode": "passive",
    "secauth": True,
    "token_coefficient": 125,
    "totem_version": 2,
}


def _safe(value: Any) -> str:
    """Strip control characters/newlines so a value cannot inject directives."""
    return _CONTROL_RE.sub("", str(value)).strip()


def _onoff(value: Any) -> str:
    return "on" if value in (True, "on", "yes", 1, "1") else "off"


def _yesno(value: Any) -> str:
    return "yes" if value in (True, "on", "yes", 1, "1") else "no"


class CorosyncConfigGenerator:
    """Builds (and versions) the Corosync config for a cluster."""

    # -- public API ---------------------------------------------------------
    def sync(self, cluster) -> str:
        """Ensure the config is current (bumping version if needed) and render.

        Called when displaying or serving the config so the file always matches
        the current set of nodes.
        """
        nodes = cluster.nodes()
        options = dict(cluster.get("corosync_config") or {})
        assigned = self.assign_node_ids(nodes)
        signature = self._signature(cluster, nodes, assigned)

        if options.get("topology") != signature:
            current = int(options.get("config_version") or 0)
            options["config_version"] = current + 1
            options["topology"] = signature
            cluster.update({"corosync_config": options})

        return self.render(cluster, nodes, assigned, options)

    def render(self, cluster, nodes, assigned, options) -> str:
        merged = {**DEFAULTS, **{k: v for k, v in options.items() if k in DEFAULTS}}
        version = int(options.get("config_version") or merged["config_version"])

        lines: list[str] = []
        lines.append("logging {")
        lines.append(f"  debug: {_onoff(options.get('logging_debug', DEFAULTS['logging_debug']))}")
        lines.append(
            f"  to_syslog: {_yesno(options.get('logging_to_syslog', DEFAULTS['logging_to_syslog']))}"
        )
        lines.append("}")
        lines.append("")

        lines.append("nodelist {")
        for node in sorted(nodes, key=lambda n: assigned.get(n.id, 0)):
            lines.append("  node {")
            lines.append(f"    name: {_safe(node.name)}")
            lines.append(f"    nodeid: {assigned.get(node.id, 0)}")
            lines.append(f"    quorum_votes: {self._quorum_votes(node)}")
            lines.append(f"    ring0_addr: {self._ring0_addr(node)}")
            lines.append("  }")
        lines.append("}")
        lines.append("")

        lines.append("quorum {")
        lines.append(f"  provider: {_safe(options.get('quorum_provider', DEFAULTS['quorum_provider']))}")
        lines.append("}")
        lines.append("")

        lines.append("totem {")
        lines.append(f"  cluster_name: {_safe(cluster.name)}")
        lines.append(f"  config_version: {version}")
        lines.append("  interface {")
        lines.append(f"    linknumber: {int(options.get('linknumber', DEFAULTS['linknumber']))}")
        lines.append("  }")
        lines.append(f"  ip_version: {_safe(options.get('ip_version', DEFAULTS['ip_version']))}")
        lines.append(f"  link_mode: {_safe(options.get('link_mode', DEFAULTS['link_mode']))}")
        lines.append(f"  secauth: {_onoff(options.get('secauth', DEFAULTS['secauth']))}")
        lines.append(f"  token_coefficient: {int(options.get('token_coefficient', DEFAULTS['token_coefficient']))}")
        lines.append(f"  version: {int(options.get('totem_version', DEFAULTS['totem_version']))}")
        lines.append("}")

        return "\n".join(lines) + "\n"

    # -- helpers ------------------------------------------------------------
    def assign_node_ids(self, nodes) -> dict[int, int]:
        """Return ``{node.id: corosync_node_id}`` with unique, stable IDs."""
        explicit: dict[int, int] = {}
        taken: set[int] = set()

        for node in nodes:
            raw = node.get("corosync_node_id")
            if raw in (None, ""):
                continue
            try:
                node_id = int(raw)
            except (TypeError, ValueError):
                continue
            if node_id < 1 or node_id in taken:
                continue  # invalid or duplicate -> reassigned below
            explicit[node.id] = node_id
            taken.add(node_id)

        next_id = 1
        for node in sorted(nodes, key=lambda n: (n.name or "").lower()):
            if node.id in explicit:
                continue
            while next_id in taken:
                next_id += 1
            explicit[node.id] = next_id
            taken.add(next_id)
        return explicit

    def _quorum_votes(self, node) -> int:
        network = node.get("network_config") or {}
        raw = network.get("quorum_votes", 1) if isinstance(network, dict) else 1
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1

    def _ring0_addr(self, node) -> str:
        network = node.get("network_config") or {}
        candidate = None
        if isinstance(network, dict):
            candidate = network.get("corosync_ring0_addr") or network.get("ring0_addr")
        if candidate:
            try:
                return Validator.ipv4(candidate)
            except ValidationError:
                pass
        return _safe(node.ip_address)

    def _signature(self, cluster, nodes, assigned) -> str:
        # Topology only — must NOT include config_version, otherwise the
        # signature would change on every render and bump forever.
        parts = [_safe(cluster.name)]
        for node in sorted(nodes, key=lambda n: assigned.get(n.id, 0)):
            parts.append(
                f"{assigned.get(node.id)}|{_safe(node.name)}|{self._ring0_addr(node)}|{self._quorum_votes(node)}"
            )
        digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        return digest
