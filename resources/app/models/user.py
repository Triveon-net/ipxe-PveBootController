"""User model."""

from __future__ import annotations

from core.hashing import hash_password, needs_rehash, verify_password
from core.model import Model
from core.support import now_iso


class User(Model):
    table = "users"
    hidden = ("password_hash",)

    def set_password(self, plain: str) -> "User":
        self.password_hash = hash_password(plain)
        return self.save()

    def check_password(self, plain: str) -> bool:
        stored = self._attributes.get("password_hash", "")
        return verify_password(plain, stored)

    def maybe_rehash(self, plain: str) -> None:
        stored = self._attributes.get("password_hash", "")
        if stored and needs_rehash(stored):
            self.set_password(plain)

    @property
    def full_name(self) -> str:
        parts = [self._attributes.get("first_name", ""), self._attributes.get("last_name", "")]
        return " ".join(p for p in parts if p).strip() or self.username

    @property
    def initials(self) -> str:
        first = self._attributes.get("first_name", "")[:1]
        last = self._attributes.get("last_name", "")[:1]
        return (first + last).upper() or self.username[:2].upper()

    def mark_login(self) -> None:
        self.last_login_at = now_iso()
        self.save()

    @property
    def is_active(self) -> bool:
        return bool(self._attributes.get("is_active", 1))
