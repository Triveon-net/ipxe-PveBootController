"""HTTP middleware.

Middleware wraps the request pipeline. `handle(request, next)` may inspect the
request, short-circuit by returning a response, or delegate to `next(request)`.
"""

from __future__ import annotations

from werkzeug.wrappers import Response

from core.context import RequestContext, reset, set_current
from core.csrf import Csrf
from core.exceptions import ApiException, TokenMismatch
from core.response import make_response, redirect


class Middleware:
    def handle(self, request, next_handler):
        return next_handler(request)


class CloseDatabase(Middleware):
    """Close the request thread's DB connection after every request.

    Prevents file-descriptor leaks on threaded servers (a new thread per
    request would otherwise leave its SQLite connection open).
    """

    def __init__(self, db) -> None:
        self.db = db

    def handle(self, request, next_handler):
        try:
            return next_handler(request)
        finally:
            self.db.close()


class StartSession(Middleware):
    def __init__(self, app) -> None:
        self.app = app

    def handle(self, request, next_handler):
        session = self.app.sessions.start(request)
        ctx = RequestContext(request, session)
        ctx.csrf_token = session.csrf_token
        ctx.user = self.app.auth.load_user(session.user_id)
        ctx.flashes = session.take_flashes()

        token = set_current(ctx)
        try:
            response = next_handler(request)
        finally:
            reset(token)

        self._set_cookie(response, session)
        return response

    def _set_cookie(self, response: Response, session) -> None:
        config = self.app.config
        response.set_cookie(
            config.session_cookie,
            session.token,
            max_age=config.session_lifetime * 60,
            httponly=True,
            secure=config.session_secure,
            samesite="Lax",
            path="/",
        )


class AddSecurityHeaders(Middleware):
    def __init__(self, config=None) -> None:
        self.config = config

    def handle(self, request, next_handler):
        response = next_handler(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # Allow assets served from APP_URL even if the panel is opened via a
        # different host (e.g. IP vs. hostname).
        origin = (self.config.url if self.config else "") or ""
        extra = f" {origin}" if origin else ""
        # HTMX + Tailwind CDN + Font Awesome CDN + inline styles/scripts.
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            f"https://unpkg.com https://cdn.tailwindcss.com{extra}; "
            "style-src 'self' 'unsafe-inline' "
            f"https://cdnjs.cloudflare.com https://fonts.googleapis.com{extra}; "
            "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:; "
            f"img-src 'self' data:{extra}; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        return response


class VerifyCsrfToken(Middleware):
    def handle(self, request, next_handler):
        if not Csrf.verify(request):
            raise TokenMismatch()
        return next_handler(request)


class Authenticate(Middleware):
    def handle(self, request, next_handler):
        from core import auth

        if auth.check():
            return next_handler(request)

        if _wants_json(request):
            raise ApiException("Unauthenticated.", status_code=401)

        from core.context import get_context

        get_context().session.put("intended", request.path)
        return redirect("/login")


class RedirectIfAuthenticated(Middleware):
    def handle(self, request, next_handler):
        from core import auth

        if auth.check():
            return redirect("/dashboard")
        return next_handler(request)


class ApiToken(Middleware):
    """Optional shared-secret guard for machine-to-machine API routes.

    If a non-empty ``api.token`` setting exists, every request must present it
    in the ``X-API-Token`` header. Machine clients (booting nodes) cannot use
    sessions/CSRF, so this is their authentication mechanism.
    """

    def handle(self, request, next_handler):
        import hmac

        from core.exceptions import ApiException
        from resources.app.models.setting import Setting

        expected = Setting.get("api.token") or ""
        if not expected:
            # No token configured: leave the endpoint open (documented default).
            return next_handler(request)

        provided = request.headers.get("X-API-Token", "")
        if not provided or not hmac.compare_digest(provided, expected):
            raise ApiException("Invalid or missing API token.", status_code=401)
        return next_handler(request)


def _wants_json(request) -> bool:
    if request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def error_response(app, request, status: int, message: str) -> Response:
    """Render an error page (HTML) or JSON body depending on the request."""
    if _wants_json(request):
        return app.json({"error": message}, status=status)
    template = f"errors/{status}.html"
    if app.view.exists(template):
        body = app.view.render(template, {"status": status, "message": message})
    else:
        body = app.view.render(
            "errors/error.html", {"status": status, "message": message}
        )
    return make_response(body, status=status)
