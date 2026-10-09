"""Storage model (NFS shares today; extensible via `kind`)."""

from __future__ import annotations

from typing import Iterable

from core.model import Model


class Storage(Model):
    table = "storages"
    casts = {"options": "json"}

    @property
    def label(self) -> str:
        return f"{self.server}:{self.export}"

    # -- reads --------------------------------------------------------------
    @classmethod
    def for_node(cls, node_id: int) -> list["Storage"]:
        rows = cls.db().query(
            "SELECT s.* FROM storages s "
            "JOIN node_storage ns ON ns.storage_id = s.id "
            "WHERE ns.node_id = ? ORDER BY s.storage_id",
            (node_id,),
        )
        return [cls.hydrate(row) for row in rows]

    @classmethod
    def for_cluster(cls, cluster_id: int) -> list["Storage"]:
        rows = cls.db().query(
            "SELECT s.* FROM storages s "
            "JOIN cluster_storage cs ON cs.storage_id = s.id "
            "WHERE cs.cluster_id = ? ORDER BY s.storage_id",
            (cluster_id,),
        )
        return [cls.hydrate(row) for row in rows]

    @classmethod
    def ids_for_node(cls, node_id: int) -> list[int]:
        rows = cls.db().query(
            "SELECT storage_id FROM node_storage WHERE node_id = ?", (node_id,)
        )
        return [row["storage_id"] for row in rows]

    @classmethod
    def ids_for_cluster(cls, cluster_id: int) -> list[int]:
        rows = cls.db().query(
            "SELECT storage_id FROM cluster_storage WHERE cluster_id = ?", (cluster_id,)
        )
        return [row["storage_id"] for row in rows]

    # -- writes -------------------------------------------------------------
    @classmethod
    def _valid_ids(cls, storage_ids: Iterable) -> list[int]:
        clean: list[int] = []
        for value in storage_ids:
            try:
                clean.append(int(value))
            except (TypeError, ValueError):
                continue
        if not clean:
            return []
        placeholders = ", ".join("?" for _ in clean)
        rows = cls.db().query(
            f"SELECT id FROM storages WHERE id IN ({placeholders})", clean
        )
        existing = {row["id"] for row in rows}
        return [sid for sid in clean if sid in existing]

    @classmethod
    def sync_for_node(cls, node_id: int, storage_ids: Iterable) -> None:
        db = cls.db()
        db.run("DELETE FROM node_storage WHERE node_id = ?", (node_id,))
        for storage_id in cls._valid_ids(storage_ids):
            db.run(
                "INSERT OR IGNORE INTO node_storage (node_id, storage_id) VALUES (?, ?)",
                (node_id, storage_id),
            )

    @classmethod
    def sync_for_cluster(cls, cluster_id: int, storage_ids: Iterable) -> None:
        db = cls.db()
        db.run("DELETE FROM cluster_storage WHERE cluster_id = ?", (cluster_id,))
        for storage_id in cls._valid_ids(storage_ids):
            db.run(
                "INSERT OR IGNORE INTO cluster_storage (cluster_id, storage_id) VALUES (?, ?)",
                (cluster_id, storage_id),
            )
