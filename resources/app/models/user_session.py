"""Server-side user session model."""

from __future__ import annotations

from core.model import Model


class UserSession(Model):
    table = "sessions"
    casts = {"payload": "json"}

    @classmethod
    def _timestamps(cls) -> bool:
        return False
