"""Cluster-wide secret material: Corosync authkey + node SSH keys.

A Proxmox cluster only forms when every node shares the same Corosync authkey
(``/etc/corosync/authkey``) and trusts the other nodes' SSH keys
(``/etc/pve/priv/authorized_keys`` / ``known_hosts``). The panel is the single
source of truth for this material and hands it to each node through the config
payload, so it is identical everywhere and survives a re-provision.

The Corosync authkey is pure random bytes. SSH keys are generated with
``ssh-keygen`` (from `openssh-client`, see apt.list) so we do not pull in a
crypto dependency. Private keys are marked `hidden` on the models so they are
never serialised into API/JSON responses.
"""

from __future__ import annotations

import base64
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

AUTHKEY_BYTES = 128  # what `corosync-keygen` writes
_SSH_KEYGEN = "ssh-keygen"


def _generate_keypair(comment: str) -> tuple[str, str]:
    """Return ``(private_pem, public_line)`` or ``("", "")`` if ssh-keygen is absent."""
    if not shutil.which(_SSH_KEYGEN):
        return "", ""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "key"
        result = subprocess.run(
            [_SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return "", ""
        private = path.read_text(encoding="utf-8")
        public = Path(f"{path}.pub").read_text(encoding="utf-8").strip()
    return private, public


def _pubkey_parts(public: str) -> tuple[str, str]:
    """Split an OpenSSH public key line into ``(keytype, base64)``."""
    tokens = (public or "").split()
    if len(tokens) >= 2:
        return tokens[0], tokens[1]
    return "", ""


class ClusterKeys:
    # -- Corosync authkey ---------------------------------------------------
    @classmethod
    def ensure_authkey(cls, cluster) -> str:
        """Return the cluster's base64 authkey, generating it once if missing."""
        existing = cluster.get("authkey") or ""
        if existing:
            return existing
        encoded = base64.b64encode(secrets.token_bytes(AUTHKEY_BYTES)).decode("ascii")
        cluster.update({"authkey": encoded})
        return encoded

    # -- per-node SSH keys --------------------------------------------------
    @classmethod
    def ensure_node_keys(cls, node):
        """Generate the node's login + host keypairs if they do not exist yet.

        Re-reads from the database when the passed instance is incomplete, so a
        stale instance can never rotate keys that are already stored. Returns the
        node instance that carries the (now complete) key material.
        """
        fields = (
            "ssh_private_key",
            "ssh_public_key",
            "ssh_host_private_key",
            "ssh_host_public_key",
        )
        if all(node.get(field) for field in fields):
            return node

        model = node.__class__
        current = model.find(node.id) if getattr(node, "id", None) else None
        if current is None:
            current = node
        if all(current.get(field) for field in fields):
            return current

        updates: dict[str, str] = {}
        if not (current.get("ssh_private_key") and current.get("ssh_public_key")):
            private, public = _generate_keypair(f"pve-node:{node.name}")
            if private:
                updates["ssh_private_key"] = private
                updates["ssh_public_key"] = public
        if not (
            current.get("ssh_host_private_key") and current.get("ssh_host_public_key")
        ):
            private, public = _generate_keypair(f"pve-host:{node.name}")
            if private:
                updates["ssh_host_private_key"] = private
                updates["ssh_host_public_key"] = public

        if updates:
            current.update(updates)
        return current

    @classmethod
    def node_payload(cls, node) -> dict:
        return {
            "private": node.get("ssh_private_key", ""),
            "public": node.get("ssh_public_key", ""),
        }

    # -- cluster bundle -----------------------------------------------------
    @classmethod
    def bundle(cls, cluster) -> dict:
        """authorized_keys + known_hosts for every node in the cluster."""
        authorized: list[str] = []
        known: list[str] = []
        for node in cluster.nodes():
            node = cls.ensure_node_keys(node)

            public = node.get("ssh_public_key", "")
            if public:
                authorized.append(public)

            keytype, keydata = _pubkey_parts(node.get("ssh_host_public_key", ""))
            if keytype and keydata:
                for host in dict.fromkeys(filter(None, (node.name, node.ip_address))):
                    known.append(f"{host} {keytype} {keydata}")

        return {
            "authorized_keys": "\n".join(authorized),
            "known_hosts": "\n".join(known),
        }

    @classmethod
    def status(cls, cluster) -> dict:
        """Small summary for the panel (no key material)."""
        nodes = cluster.nodes()
        keyed = sum(1 for n in nodes if n.get("ssh_public_key"))
        return {
            "authkey": bool(cluster.get("authkey")),
            "nodes": len(nodes),
            "keyed": keyed,
        }
