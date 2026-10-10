"""Base controller with view/response/validation helpers."""

from __future__ import annotations

import json
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
        """Tell HTMX to perform a client-side redirect (full page load)."""
        return make_response("", status=204, headers={"HX-Redirect": url})

    def content_refresh(self, request: Request, url: str) -> Response:
        """React to a write action without reloading the whole page.

        For HTMX requests the target page's content fragment is rendered and
        swapped into ``#app-content`` (target/reswap overridden via response
        headers, URL pushed to history). Flash messages are delivered as an
        ``app:flash`` client event instead of relying on a page reload, so the
        sidebar, title and toasts all update in place. Non-HTMX requests fall
        back to a plain redirect (flash survives in the session).
        """
        if not is_htmx(request):
            return self.redirect(url)

        ctx = get_context()
        # The session was drained of flashes at the start of this request, so
        # anything present now was added while handling the write action.
        pending = ctx.session.take_flashes()

        try:
            body = self._render_route(request, url)
        except Exception:
            # If the target cannot be rendered, fall back to a real redirect so
            # the user still gets feedback rather than a broken fragment.
            for category, message in pending:
                ctx.session.flash(message, category)
            return self.htmx_redirect(url)

        response = make_response(body, status=200)
        response.headers["HX-Retarget"] = "#app-content"
        response.headers["HX-Reswap"] = "innerHTML"
        response.headers["HX-Push-Url"] = url
        if pending:
            response.headers["HX-Trigger"] = json.dumps(
                {
                    "app:flash": [
                        {"category": category, "message": message}
                        for category, message in pending
                    ]
                }
            )
        return response

    def _render_route(self, request: Request, url: str) -> str:
        """Render the content fragment for ``url`` by dispatching an internal
        GET, reusing the current request context (session, user, CSRF)."""
        from werkzeug.test import EnvironBuilder

        builder = EnvironBuilder(
            path=url, method="GET", headers=[("HX-Request", "true")]
        )
        target = Request(builder.get_environ())
        route, args = self.app.router.match(target)

        handler = route.handler
        if callable(handler):
            result = handler(target, **args)
        else:
            controller_name, method_name = handler.split("@", 1)
            controller_cls = self.app._resolve_controller(controller_name)
            controller = controller_cls(self.app)
            result = getattr(controller, method_name)(target, **args)

        response = self.app._normalise(result)
        return response.get_data(as_text=True)

    def _done(self, request: Request, url: str) -> Response:
        """Finish a write action by re-rendering ``url`` in place (HTMX) or
        redirecting there (plain form submit)."""
        return self.content_refresh(request, url)

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
