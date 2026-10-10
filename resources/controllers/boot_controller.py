"""Boot controller: edit the iPXE boot script options with live preview."""

from __future__ import annotations

from core.exceptions import ValidationException
from resources.app.models.boot_file import BootFile
from resources.app.services.boot_config import DEFAULTS, TOGGLES, BootConfig
from resources.app.services.boot_files import BootFiles

# Name of the on-disk file the generated script is written to on every save.
GENERATED_BOOT_FILE = "boot.ipxe"
from resources.controllers.controller import Controller

TEXT_RULES = {
    "controller_url": "nullable|string|max:255",
    "files_url": "nullable|string|max:511",
    "next_server": "nullable|host_or_ip",
    "boot_file": "nullable|string|max:128",
    "interface": "nullable|string|max:32",
    "client_ip": "nullable|ipv4",
    "client_netmask": "nullable|netmask",
    "client_gateway": "nullable|ipv4",
    "client_dns": "nullable|string|max:255",
    "action": "required|in:chain,kernel,exit,shell",
    "kernel_file": "nullable|string|max:128",
    "initrd_file": "nullable|string|max:128",
    "squashfs_file": "nullable|string|max:128",
    "chain_url": "nullable|string|max:512",
    "kernel_url": "nullable|string|max:512",
    "initrd_url": "nullable|string|max:512",
    "squashfs_url": "nullable|string|max:512",
    "kernel_cmdline": "nullable|string|max:1024",
    "boot_args": "nullable|string|max:1024",
    "sleep_seconds": "nullable|integer|min:0|max:3600",
    "prompt_timeout": "nullable|integer|min:0|max:3600",
    "variables": "nullable|string|max:8192",
    "custom_lines": "nullable|string|max:8192",
}


class BootController(Controller):
    def index(self, request):
        values = BootConfig.load()
        script = BootConfig.render(values)
        # Bootstrap the on-disk boot.ipxe once, so the download/preview works
        # before the first save.
        if not (self.config.bootfiles_path / GENERATED_BOOT_FILE).is_file():
            BootFiles.store_text(GENERATED_BOOT_FILE, script)
        return self.render_page(
            "pages/boot/index.html",
            {
                "values": values,
                "script": script,
                "files": BootFile.query().order_by("name").get(),
                "active": "boot",
            },
            request,
        )

    def update(self, request):
        try:
            self.validate(request.form, TEXT_RULES)
        except ValidationException as exc:
            self.flash(self.first_error(exc), "error")
            return self._done(request, "/boot")

        config = self._config_from_form(request)
        BootConfig.save(config)
        # Materialise the generated script as a boot file so a static file
        # server / DHCP option 67 always serves the current version.
        BootFiles.store_text(GENERATED_BOOT_FILE, BootConfig.render(config))
        self.flash("Boot script settings saved.", "success")
        return self._done(request, "/boot")

    def preview(self, request):
        config = self._config_from_form(request)
        return self.render(
            "partials/boot_preview.html", {"script": BootConfig.render(config)}
        )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _config_from_form(request) -> dict:
        config = dict(DEFAULTS)
        for key in TEXT_RULES:
            config[key] = request.form.get(key, "")
        for key in ("sleep_seconds", "prompt_timeout"):
            try:
                config[key] = int(config.get(key) or 0)
            except (TypeError, ValueError):
                config[key] = 0
        for toggle in TOGGLES:
            config[toggle] = request.form.get(toggle) is not None
        return config
