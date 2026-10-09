"""HTTP router built on Werkzeug's routing map.

Routes are registered Laravel-style::

    router.get("/nodes", "NodesController@index").name("nodes.index")
    router.post("/nodes", "NodesController@store").middleware("auth")

`group()` adds a common prefix and/or middleware to everything inside it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import urlsplit

from werkzeug.routing import Map, MapAdapter, Rule, RequestRedirect

from core.exceptions import MethodNotAllowed, NotFound

Handler = str | Callable


@dataclass
class Route:
    methods: tuple[str, ...]
    path: str
    handler: Handler
    endpoint: str
    name: str | None = None
    middleware: tuple[str, ...] = ()
    is_api: bool = False


class _RouteBuilder:
    """Fluent wrapper returned by the HTTP verb helpers (Laravel-style)."""

    def __init__(self, router: "Router", route: Route):
        self._router = router
        self._route = route

    def name(self, name: str) -> "_RouteBuilder":
        self._router._names[name] = self._route.endpoint
        self._route.name = name
        return self

    def middleware(self, *names: str) -> "_RouteBuilder":
        self._route.middleware = tuple(self._route.middleware) + tuple(names)
        return self

    def __iter__(self):
        # Allows `Route(r).middleware(...)` destructuring if needed.
        return iter((self._route,))


class Router:
    def __init__(self, base_url: str = "http://127.0.0.1") -> None:
        self._map = Map()
        self._routes: dict[str, Route] = {}
        self._names: dict[str, str] = {}
        self._counter = 0
        self._stack: list[tuple[str, tuple[str, ...], bool]] = []
        # Host/scheme from APP_URL, used only for absolute URLs (see url_for).
        self._host, self._scheme = self._split_base(base_url)
        self._adapter: MapAdapter | None = None

    @staticmethod
    def _split_base(base_url: str) -> tuple[str, str]:
        parts = urlsplit(base_url if "://" in (base_url or "") else f"http://{base_url}")
        return (parts.netloc or "127.0.0.1"), (parts.scheme or "http")

    # -- grouping -----------------------------------------------------------
    @contextmanager
    def group(
        self, *, prefix: str = "", middleware: tuple[str, ...] = (), is_api: bool = False
    ) -> Iterator["Router"]:
        self._stack.append((prefix, tuple(middleware), is_api))
        try:
            yield self
        finally:
            self._stack.pop()

    def _resolved(self) -> tuple[str, tuple[str, ...], bool]:
        prefix = "".join(item[0] for item in self._stack)
        middleware: tuple[str, ...] = ()
        for item in self._stack:
            middleware += item[1]
        is_api = any(item[2] for item in self._stack)
        return prefix, middleware, is_api

    # -- registration -------------------------------------------------------
    def add(
        self,
        methods: list[str] | tuple[str, ...],
        path: str,
        handler: Handler,
        *,
        name: str | None = None,
        middleware: tuple[str, ...] = (),
        is_api: bool = False,
    ) -> Route:
        prefix, group_mw, group_api = self._resolved()
        full_path = (prefix + path) or "/"
        if not full_path.startswith("/"):
            full_path = "/" + full_path
        methods_upper = tuple(sorted({m.upper() for m in methods}))
        self._counter += 1
        endpoint = f"route_{self._counter}"

        rule = Rule(full_path, methods=methods_upper, endpoint=endpoint)
        self._map.add(rule)

        route = Route(
            methods=methods_upper,
            path=full_path,
            handler=handler,
            endpoint=endpoint,
            middleware=group_mw + tuple(middleware),
            is_api=is_api or group_api,
        )
        self._routes[endpoint] = route
        if name:
            self._names[name] = endpoint
        return route

    def get(self, path: str, handler: Handler, **kwargs) -> _RouteBuilder:
        return _RouteBuilder(self, self.add(("GET",), path, handler, **kwargs))

    def post(self, path: str, handler: Handler, **kwargs) -> _RouteBuilder:
        return _RouteBuilder(self, self.add(("POST",), path, handler, **kwargs))

    def put(self, path: str, handler: Handler, **kwargs) -> _RouteBuilder:
        return _RouteBuilder(self, self.add(("PUT",), path, handler, **kwargs))

    def patch(self, path: str, handler: Handler, **kwargs) -> _RouteBuilder:
        return _RouteBuilder(self, self.add(("PATCH",), path, handler, **kwargs))

    def delete(self, path: str, handler: Handler, **kwargs) -> _RouteBuilder:
        return _RouteBuilder(self, self.add(("DELETE",), path, handler, **kwargs))

    def any(self, path: str, handler: Handler, **kwargs) -> _RouteBuilder:
        return _RouteBuilder(
            self, self.add(("GET", "POST", "PUT", "PATCH", "DELETE"), path, handler, **kwargs)
        )

    # -- matching -----------------------------------------------------------
    def match(self, request) -> tuple[Route, dict]:
        adapter = self._map.bind_to_environ(request.environ)
        try:
            endpoint, values = adapter.match()
        except RequestRedirect:
            raise
        except MethodNotAllowed:
            raise
        except Exception as exc:  # werkzeug NotFound and friends
            raise NotFound() from exc
        route = self._routes.get(endpoint)
        if route is None:
            raise NotFound()
        return route, dict(values)

    def routes(self) -> list[Route]:
        return list(self._routes.values())

    # -- URL generation -----------------------------------------------------
    def url_for(self, name: str, absolute: bool = False, **values) -> str:
        endpoint = self._names.get(name)
        if endpoint is None:
            raise KeyError(f"Unknown route name: {name}")
        if self._adapter is None:
            self._adapter = self._map.bind(self._host, url_scheme=self._scheme)
        # Relative paths by default, so links always follow the host the
        # browser is on. Pass absolute=True for a full URL built from APP_URL.
        return self._adapter.build(
            endpoint, values, append_unknown=False, force_external=absolute
        )
