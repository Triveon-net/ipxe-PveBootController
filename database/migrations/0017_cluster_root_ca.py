"""Store the cluster's root CA key/cert in the panel.

`pvecm updatecerts` signs each node's certificate with the cluster root CA
(`/etc/pve/priv/pve-root-ca.key` + `/etc/pve/pve-root-ca.pem`). That material is
cluster-wide and must be byte-identical on every node, exactly like the Corosync
authkey, but it was never stored. On a re-provisioned node it was missing and
`pvecm updatecerts` failed with "Could not find private key from
/etc/pve/priv/pve-root-ca.key". The panel now generates it once and hands it to
each node.
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute("ALTER TABLE clusters ADD COLUMN root_ca_key TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE clusters ADD COLUMN root_ca_pem TEXT NOT NULL DEFAULT ''")


def down(conn) -> None:
    # Columns are additive; nothing destructive to revert.
    pass
