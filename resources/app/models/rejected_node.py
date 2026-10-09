"""MAC addresses that were rejected from the pending list."""

from __future__ import annotations

from core.model import Model
from core.support import now_iso


class RejectedNode(Model):
    table = "rejected_nodes"

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    @classmethod
    def exists(cls, mac_address: str) -> bool:
        return cls.where("mac_address", mac_address).exists()

    @classmethod
    def reject(cls, mac_address: str, reason: str = "") -> "RejectedNode":
        existing = cls.where("mac_address", mac_address).first()
        if existing is not None:
            return existing
        return cls.create(
            {"mac_address": mac_address, "reason": reason or "", "created_at": now_iso()}
        )

    @classmethod
    def restore(cls, mac_address: str) -> int:
        return cls.where("mac_address", mac_address).delete()
