"""Small framework-level helpers (time, ids, misc)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_timestamp() -> int:
    """Current UTC time as a Unix timestamp (int)."""
    return int(datetime.now(timezone.utc).timestamp())


def random_token(nbytes: int = 32) -> str:
    """URL-safe cryptographically secure random token."""
    return secrets.token_urlsafe(nbytes)


def slugify(value: str) -> str:
    import re

    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return value.strip("-") or "item"
