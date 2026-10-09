"""Base controller with view/response/validation helpers."""

from __future__ import annotations

from typing import Any, Mapping

from core.context import get_context
from core.response import json_response, make_response, redirect
from core.validation import validate as run_validation
from werkzeug.wrappers import Request, Response


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


class Controller:
    def __init__(self, app) -> None:
        self.app = app
        self.config = app.config

    # -- views --------------------------------------------------------------
    def render(self, template: str, context: Mapping[str, Any] | None = None) -> Response:
        return make_response(self.app.view.render(template, dict(context or {})))

    def render_page(
        self,
        template: str,
        context: Mapping[str, Any] | None = None,
        request: Request | None = None,
        *,
        layout: str = "layout/app.html",
        partial: str = "layout/partial.html",
    ) -> Response:
        """Render a page, choosing layout vs. bare partial for HTMX navigation."""
        ctx = dict(context or {})
        if request is not None and is_htmx(request):
            ctx["layout"] = partial
        else:
            ctx["layout"] = layout
        return self.render(template, ctx)

    # -- responses ----------------------------------------------------------
    def json(self, data: Any, status: int = 200) -> Response:
        return json_response(data, status=status)

    def redirect(self, url: str, status: int = 302) -> Response:
        return redirect(url, status=status)

    def back(self, fallback: str = "/") -> Response:
        ctx = get_context()
        target = fallback
        if ctx is not None:
            target = ctx.request.headers.get("Referer") or ctx.session.get("intended") or fallback
        return redirect(target)

    def htmx_redirect(self, url: str) -> Response:
        """Tell HTMX to perform a client-side redirect."""
        return make_response("", status=204, headers={"HX-Redirect": url})

    # -- session helpers ----------------------------------------------------
    @property
    def session(self):
        return get_context().session

    @property
    def user(self):
        from core import auth

        return auth.user()

    def flash(self, message: str, category: str = "success") -> None:
        ctx = get_context()
        if ctx is not None:
            ctx.session.flash(message, category)

    def old(self, key: str, default: Any = "") -> Any:
        ctx = get_context()
        if ctx is None:
            return default
        value = ctx.request.form.get(key)
        return default if value is None else value

    # -- validation ---------------------------------------------------------
    def validate(
        self,
        data: Mapping[str, Any],
        rules: Mapping[str, str | list[str]],
    ) -> dict[str, Any]:
        return run_validation(data, rules)

    @staticmethod
    def first_error(exc: Exception) -> str:
        """Return the first validation message from a ValidationException."""
        errors = getattr(exc, "errors", {}) or {}
        for messages in errors.values():
            if messages:
                return messages[0]
        return getattr(exc, "message", "Please check the input.")
