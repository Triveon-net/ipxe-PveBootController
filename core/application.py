"""The application kernel.

Wires configuration, database, router, view engine, auth and middleware
together and exposes a WSGI callable.
"""

from __future__ import annotations

import importlib
import re
import traceback
from typing import Any, Callable

from werkzeug.exceptions import HTTPException as WerkzeugHTTPException
from werkzeug.middleware.shared_data import SharedDataMiddleware
from werkzeug.routing import RequestRedirect
from werkzeug.wrappers import Request, Response

from config import Config, get_config
from core.auth import AuthManager
from core.exceptions import ApiException, HttpException
from core.middleware import (
    AddSecurityHeaders,
    ApiToken,
    Authenticate,
    Middleware,
    RedirectIfAuthenticated,
    StartSession,
    VerifyCsrfToken,
    error_response,
)
from core.response import json_response, make_response, redirect
from core.router import Router
from core.session import SessionStore
from core.view import ViewFactory
from database import get_db
from database.migrator import Migrator

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def snake_case(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


class Application:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self.db = get_db()
        self.router = Router(self.config.url)
        self.view = ViewFactory(self.config, self.router)
        self.auth = AuthManager(self.db, self.config)
        self.sessions = SessionStore(self.db, self.config)

        self.middleware: list[Middleware] = [
            AddSecurityHeaders(self.config),
            StartSession(self),
        ]
        self.middleware_map: dict[str, Middleware] = {
            "csrf": VerifyCsrfToken(),
            "auth": Authenticate(),
            "guest": RedirectIfAuthenticated(),
            "api_token": ApiToken(),
        }
        self._booted = False

        # Serve `/static/*` from `resources/static` at the WSGI layer.
        self.config.static_path.mkdir(parents=True, exist_ok=True)
        self._wsgi = SharedDataMiddleware(
            self._handle_wsgi, {"/static": str(self.config.static_path)}
        )

    # -- bootstrap ----------------------------------------------------------
    def boot(self) -> "Application":
        if self._booted:
            return self
        # Make sure the schema exists before anything touches the database.
        try:
            self.migrate()
        except Exception:
            # Let the caller decide how to surface a broken database.
            if self.config.debug:
                raise
        from routes import api as api_routes
        from routes import web as web_routes

        web_routes.register(self.router)
        api_routes.register(self.router)
        self._booted = True
        return self

    def migrate(self) -> list[str]:
        return Migrator(self.db, self.config.migrations_path).run()

    # -- WSGI ---------------------------------------------------------------
    def __call__(self, environ, start_response):
        return self._wsgi(environ, start_response)

    def _handle_wsgi(self, environ, start_response):
        request = Request(environ)
        response = self.handle(request)
        return response(environ, start_response)

    def handle(self, request: Request) -> Response:
        handler: Callable[[Request], Response] = self._dispatch
        for mw in reversed(self.middleware):
            handler = self._wrap(mw, handler)
        try:
            return handler(request)
        except ApiException as exc:
            return json_response(
                {"error": exc.message, "errors": getattr(exc, "errors", None)},
                status=exc.status_code,
            )
        except RequestRedirect as exc:
            # Must come before the generic HTTPException handler: RequestRedirect
            # is a subclass of werkzeug's HTTPException.
            return redirect(exc.new_url, status=exc.code or 308)
        except HttpException as exc:
            return self._render_error(request, exc.status_code, exc.message)
        except WerkzeugHTTPException as exc:
            return self._render_error(
                request, exc.code or 500, exc.description or "Error"
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._handle_unexpected(request, exc)

    @staticmethod
    def _wrap(mw: Middleware, nxt):
        return lambda request: mw.handle(request, nxt)

    def _dispatch(self, request: Request) -> Response:
        route, args = self.router.match(request)
        chain = self._apply_route_middleware(route)
        return chain(request, args)

    def _apply_route_middleware(self, route):
        def terminal(request: Request, args: dict) -> Response:
            return self._call_handler(route, request, args)

        chain = terminal
        for name in reversed(route.middleware):
            mw = self.middleware_map.get(name)
            if mw is None:
                raise RuntimeError(f"Unknown middleware: {name!r}")
            inner = chain

            def linked(request, args, _mw=mw, _inner=inner):
                return _mw.handle(request, lambda req: _inner(req, args))

            chain = linked
        return chain

    # -- handler resolution -------------------------------------------------
    def _call_handler(self, route, request: Request, args: dict) -> Response:
        handler = route.handler
        if callable(handler):
            return self._normalise(handler(request, **args))

        if "@" not in handler:
            raise RuntimeError(f"Invalid route handler: {handler!r}")
        controller_name, method_name = handler.split("@", 1)
        controller_cls = self._resolve_controller(controller_name)
        controller = controller_cls(self)
        if not hasattr(controller, method_name):
            raise RuntimeError(f"Controller method not found: {handler!r}")
        result = getattr(controller, method_name)(request, **args)
        return self._normalise(result)

    def _resolve_controller(self, name: str):
        name = name.replace("\\", "/").replace(".", "/")
        parts = [p for p in name.split("/") if p]
        class_name = parts[-1]
        namespace = [snake_case(p) for p in parts[:-1]]
        module_path = ".".join(["resources", "controllers", *namespace, snake_case(class_name)])
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"Controller not found: {name!r}") from exc
        controller_cls = getattr(module, class_name, None)
        if controller_cls is None:
            raise RuntimeError(f"Controller class not found: {class_name!r}")
        return controller_cls

    @staticmethod
    def _normalise(result: Any) -> Response:
        if result is None:
            return make_response("", status=204)
        if isinstance(result, Response):
            return result
        if isinstance(result, dict):
            return json_response(result)
        return make_response(str(result))

    # -- error handling -----------------------------------------------------
    def _render_error(self, request: Request, status: int, message: str) -> Response:
        return error_response(self, request, status, message)

    def _handle_unexpected(self, request: Request, exc: Exception) -> Response:
        if self.config.debug:
            from core.security import Sanitizer

            body = Sanitizer.escape(traceback.format_exc())
            return make_response(f"<pre>{body}</pre>", status=500)
        return self._render_error(request, 500, "Internal Server Error")

    # -- conveniences used by controllers -----------------------------------
    def render(self, template: str, context: dict | None = None) -> Response:
        return make_response(self.view.render(template, context))

    def json(self, data: Any, status: int = 200) -> Response:
        return json_response(data, status=status)

    def redirect(self, url: str, status: int = 302) -> Response:
        return redirect(url, status=status)

    def url_for(self, name: str, **values) -> str:
        return self.router.url_for(name, **values)
