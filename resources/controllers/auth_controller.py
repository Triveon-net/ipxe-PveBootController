"""Authentication controller (login / logout)."""

from __future__ import annotations

from resources.controllers.controller import Controller, is_htmx


class AuthController(Controller):
    def showLogin(self, request):
        from core import auth

        if auth.check():
            return self.redirect("/dashboard")
        return self.render_page(
            "pages/auth/login.html",
            {"error": None, "username": ""},
            request,
            layout="layout/auth.html",
        )

    def login(self, request):
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        ok, error = self.app.auth.attempt(request, self.session, username, password)

        if not ok:
            return self.render_page(
                "pages/auth/login.html",
                {"error": error, "username": username},
                request,
                layout="layout/auth.html",
            )

        target = self.session.get("intended") or "/dashboard"
        self.session.forget("intended")

        # Refresh the request context so helpers/templates see the logged-in user.
        from core.context import get_context

        get_context().user = self.app.auth.load_user(self.session.user_id)

        self.flash(f"Welcome back, {self.user.full_name}!", "success")

        if is_htmx(request):
            return self.htmx_redirect(target)
        return self.redirect(target)

    def logout(self, request):
        self.app.auth.logout(self.session)
        if is_htmx(request):
            return self.htmx_redirect("/login")
        return self.redirect("/login")
