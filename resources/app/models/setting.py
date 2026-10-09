"""Key/value settings model."""

from __future__ import annotations

from core.model import Model
from core.support import now_iso


class Setting(Model):
    table = "settings"
    primary_key = "key"

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    @classmethod
    def get(cls, key: str, default: str | None = None) -> str | None:
        row = cls.find(key)
        return row.value if row is not None else default

    @classmethod
    def put(cls, key: str, value: str) -> None:
        existing = cls.find(key)
        if existing is None:
            cls.create({"key": key, "value": value, "updated_at": now_iso()})
        else:
            existing.update({"value": value, "updated_at": now_iso()})
