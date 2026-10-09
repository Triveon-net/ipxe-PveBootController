"""Password hashing.

Uses `hashlib.scrypt` (memory-hard, in the standard library) with a random
per-password salt. The stored format is self-describing so parameters can be
upgraded later without invalidating existing hashes::

    scrypt$<n>$<r>$<p>$<b64 salt>$<b64 derived key>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_MAX_PASSWORD_BYTES = 1024  # guard against hashing-based DoS


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(raw, salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "$".join(
        [
            "scrypt",
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
        derived = hashlib.scrypt(
            raw, salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


def needs_rehash(stored: str) -> bool:
    """True if a stored hash uses weaker parameters than current settings."""
    try:
        scheme, n, r, p, _, _ = stored.split("$")
    except ValueError:
        return True
    return scheme != "scrypt" or int(n) != _N or int(r) != _R or int(p) != _P
