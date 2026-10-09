"""DB-backed sessions.

Only the SHA-256 hash of the session token is stored; the raw token lives
solely in the client cookie. This means a database leak does not expose usable
session tokens. Sessions are revoked server-side on logout.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from config import Config
from core.support import now_iso, now_timestamp, random_token
from database.database import Database


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Session:
    def __init__(self, store: "SessionStore", row: Any, token: str, *, is_new: bool = False):
        self.store = store
        self.row = row
        self.token = token
        self.is_new = is_new

    # -- csrf ---------------------------------------------------------------
    @property
    def csrf_token(self) -> str:
        return self.row.csrf_token

    # -- key/value payload --------------------------------------------------
    @property
    def _payload(self) -> dict:
        raw = self.row.payload
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._payload.get(key, default)

    def put(self, key: str, value: Any) -> None:
        payload = self._payload
        payload[key] = value
        self._write_payload(payload)

    def forget(self, key: str) -> None:
        payload = self._payload
        payload.pop(key, None)
        self._write_payload(payload)

    def _write_payload(self, payload: dict) -> None:
        self.row.update({"payload": json.dumps(payload)})

    # -- flash messages -----------------------------------------------------
    def flash(self, message: str, category: str = "info") -> None:
        flashes = self._payload.get("_flash", [])
        flashes.append([category, message])
        payload = self._payload
        payload["_flash"] = flashes
        self._write_payload(payload)

    def take_flashes(self) -> list[tuple[str, str]]:
        payload = self._payload
        flashes = payload.pop("_flash", [])
        self._write_payload(payload)
        return [(str(c), str(m)) for c, m in flashes]

    # -- authentication -----------------------------------------------------
    def login(self, user_id: int) -> None:
        """Attach a user and rotate the token (session-fixation protection)."""
        self.token = random_token(32)
        self.row.update(
            {
                "token_hash": _hash_token(self.token),
                "user_id": user_id,
                "last_activity": now_timestamp(),
            }
        )

    def logout(self) -> None:
        self.invalidate()

    def invalidate(self) -> None:
        try:
            self.row.delete()
        except Exception:
            pass

    # -- lifecycle ----------------------------------------------------------
    def touch(self) -> None:
        self.row.update({"last_activity": now_timestamp()})

    @property
    def user_id(self) -> int | None:
        return self.row.user_id

    @property
    def is_authenticated(self) -> bool:
        return self.row.user_id is not None


class SessionStore:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def start(self, request) -> Session:
        from resources.app.models.user_session import UserSession

        raw = request.cookies.get(self.config.session_cookie)
        session: Session | None = None
        if raw:
            row = UserSession.where("token_hash", _hash_token(raw)).first()
            if row and self._is_fresh(row):
                session = Session(self, row, raw)
        if session is None:
            session = self._create(request)
        session.touch()
        return session

    def _is_fresh(self, row) -> bool:
        age = now_timestamp() - int(row.last_activity or 0)
        return age <= self.config.session_lifetime * 60

    def _create(self, request) -> Session:
        from resources.app.models.user_session import UserSession

        token = random_token(32)
        row = UserSession.create(
            {
                "token_hash": _hash_token(token),
                "user_id": None,
                "ip_address": _client_ip(request),
                "user_agent": (request.headers.get("User-Agent") or "")[:255],
                "csrf_token": random_token(32),
                "payload": "{}",
                "last_activity": now_timestamp(),
                "created_at": now_iso(),
            }
        )
        return Session(self, row, token, is_new=True)

    def purge_expired(self) -> int:
        from resources.app.models.user_session import UserSession

        cutoff = now_timestamp() - self.config.session_lifetime * 60
        return UserSession.where("last_activity", cutoff, "<").delete()


def _client_ip(request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.remote_addr or "")[:45]


def client_ip(request) -> str:
    """Public helper for middleware/controllers."""
    return _client_ip(request)
