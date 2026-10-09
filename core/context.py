"""Per-request context.

Stored in a `contextvars.ContextVar` so helpers (`auth_user()`, `csrf_token()`)
and the view layer can reach the current request/session without threading it
through every function signature.
"""

from __future__ import annotations

import contextvars
from typing import Any

_current: contextvars.ContextVar["RequestContext | None"] = contextvars.ContextVar(
    "app_request_context", default=None
)


class RequestContext:
    def __init__(self, request: Any, session: Any) -> None:
        self.request = request
        self.session = session
        self.user: Any | None = None
        self.csrf_token: str = ""
        self.flashes: list[tuple[str, str]] = []


def current() -> "RequestContext | None":
    return _current.get()


def set_current(ctx: "RequestContext | None") -> contextvars.Token:
    return _current.set(ctx)


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


def get_context() -> "RequestContext":
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("No active request context.")
    return ctx
