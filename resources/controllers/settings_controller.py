"""Settings controller (global PXE / API configuration)."""

from __future__ import annotations

from core.exceptions import ValidationException
from core.validation import validate
from resources.app.models.setting import Setting
from resources.controllers.controller import Controller, is_htmx

# Only these keys may be written through the panel, each with its own rules.
SETTING_RULES = {
    "pxe.next_server": "required|ipv4",
    "pxe.boot_file": "required|string|max:128",
    "cluster.default_quorum": "required|integer|min:1|max:64",
    "api.token": "nullable|string|max:255",
}


class SettingsController(Controller):
    def index(self, request):
        values = {key: (Setting.get(key) or "") for key in SETTING_RULES}
        return self.render_page(
            "pages/settings/index.html",
            {"settings": values, "active": "settings"},
            request,
        )

    def update(self, request):
        collected = {key: request.form.get(key, "") for key in SETTING_RULES}
        try:
            clean = validate(collected, SETTING_RULES)
        except ValidationException as exc:
            self.flash("Please fix the highlighted errors.", "error")
            return self._done(request, "/settings")

        for key, value in clean.items():
            Setting.put(key, "" if value is None else str(value))

        self.flash("Settings saved.", "success")
        return self._done(request, "/settings")

    def _done(self, request, url: str):
        if is_htmx(request):
            return self.htmx_redirect(url)
        return self.redirect(url)
