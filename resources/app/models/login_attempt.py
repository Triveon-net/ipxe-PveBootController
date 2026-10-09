"""Login attempt model (brute-force throttling)."""

from __future__ import annotations

from core.model import Model
from core.support import now_iso


class LoginAttempt(Model):
    table = "login_attempts"

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    @classmethod
    def record(cls, username: str, ip_address: str, successful: bool) -> None:
        cls.create(
            {
                "username": username,
                "ip_address": ip_address,
                "successful": 1 if successful else 0,
                "created_at": now_iso(),
            }
        )

    @classmethod
    def recent_failures(cls, username: str, ip_address: str, since_iso: str) -> int:
        return (
            cls.where("username", username)
            .where("ip_address", ip_address)
            .where("successful", 0)
            .where("created_at", since_iso, ">=")
            .count()
        )
