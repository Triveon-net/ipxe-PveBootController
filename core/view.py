"""Jinja2 view factory.

Autoescaping is enabled for every HTML template, which is the framework's
primary XSS defence: interpolated values are HTML-escaped unless a developer
explicitly marks a value safe. Do not disable it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.support import now_iso


class ViewFactory:
    def __init__(self, config, router) -> None:
        self.config = config
        self.router = router
        self.env = Environment(
            loader=FileSystemLoader(str(config.views_path)),
            autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._register_globals()
        self._register_filters()

    # -- registration -------------------------------------------------------
    def _register_globals(self) -> None:
        from core import auth
        from core.context import current
        from core.csrf import Csrf

        def url_for(name: str, **values: Any) -> str:
            return self.router.url_for(name, **values)

        def asset(path: str) -> str:
            clean = path.lstrip("/")
            return f"{self.config.url}/static/{clean}"

        def old(key: str, default: Any = "") -> Any:
            ctx = current()
            if ctx is None:
                return default
            value = ctx.request.form.get(key)
            return default if value is None else value

        def flashes() -> list[tuple[str, str]]:
            ctx = current()
            return list(ctx.flashes) if ctx is not None else []

        def current_path() -> str:
            ctx = current()
            return ctx.request.path if ctx is not None else ""

        def auth_user():
            return auth.user()

        def auth_check() -> bool:
            return auth.check()

        self.env.globals.update(
            {
                "app_name": self.config.name,
                "app_url": self.config.url,
                "config": self.config,
                "url_for": url_for,
                "asset": asset,
                "csrf_token": Csrf.token,
                "old": old,
                "flashes": flashes,
                "current_path": current_path,
                "auth_user": auth_user,
                "auth_check": auth_check,
                "now": now_iso,
            }
        )

    def _register_filters(self) -> None:
        def format_datetime(value: Any) -> str:
            if not value:
                return "—"
            return str(value).replace("T", " ").replace("+00:00", " UTC")

        self.env.filters["datetime"] = format_datetime

    # -- rendering ----------------------------------------------------------
    def render(self, template: str, context: dict[str, Any] | None = None) -> str:
        return self.env.get_template(template).render(**(context or {}))

    def exists(self, template: str) -> bool:
        return (Path(self.config.views_path) / template).is_file()
