"""Authentication manager.

Login is protected against:
* user enumeration (single generic error message),
* brute force (per username+IP throttling window),
* session fixation (token rotation on successful login),
* timing leaks on password comparison (constant-time `verify_password`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import Config
from core.context import get_context
from core.session import client_ip
from resources.app.models.login_attempt import LoginAttempt
from resources.app.models.user import User

_GENERIC_ERROR = "Invalid username or password."


class AuthManager:
    def __init__(self, db, config: Config):
        self.db = db
        self.config = config

    def load_user(self, user_id: int | None) -> User | None:
        if not user_id:
            return None
        user = User.find(user_id)
        if user is None or not user.is_active:
            return None
        return user

    def attempt(self, request, session, username: str, password: str) -> tuple[bool, str | None]:
        username = (username or "").strip()
        ip = client_ip(request)

        since = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.config.login_decay_seconds)
        ).replace(microsecond=0).isoformat()

        if username:
            failures = LoginAttempt.recent_failures(username, ip, since)
            if failures >= self.config.login_max_attempts:
                return False, "Too many failed attempts. Please try again later."

        user = User.where("username", username).first() if username else None
        ok = bool(user is not None and user.is_active and user.check_password(password))

        if not ok:
            LoginAttempt.record(username or "-", ip, False)
            return False, _GENERIC_ERROR

        session.login(user.id)
        user.mark_login()
        user.maybe_rehash(password)
        LoginAttempt.record(username, ip, True)
        return True, None

    def logout(self, session) -> None:
        session.logout()


# --------------------------------------------------------------------------
# Helper functions that read the current request context
# --------------------------------------------------------------------------
def user() -> User | None:
    ctx = get_context()
    return ctx.user


def check() -> bool:
    ctx = get_context()
    return ctx.user is not None


def id() -> int | None:  # noqa: A001 - mirrors Laravel's `auth()->id()`
    ctx = get_context()
    return ctx.user.id if ctx.user is not None else None
