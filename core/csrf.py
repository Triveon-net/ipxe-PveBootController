"""CSRF protection.

The token lives in the server-side session and is compared in constant time
against either the `X-CSRF-Token` header (used by HTMX/JSON calls) or the
`_token` form field. Unsafe methods on the `web` middleware group are rejected
without a valid token.
"""

from __future__ import annotations

import hmac

from core.context import get_context

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Csrf:
    @staticmethod
    def token() -> str:
        try:
            return get_context().csrf_token
        except RuntimeError:
            return ""

    @staticmethod
    def submitted_token(request) -> str | None:
        token = request.headers.get("X-CSRF-Token")
        if token:
            return token
        # `form` parses the body; only touch it for non-GET requests.
        if request.method not in SAFE_METHODS:
            token = request.form.get("_token")
        return token

    @classmethod
    def verify(cls, request) -> bool:
        if request.method in SAFE_METHODS:
            return True
        expected = cls.token()
        submitted = cls.submitted_token(request)
        if not expected or not submitted:
            return False
        return hmac.compare_digest(submitted, expected)
