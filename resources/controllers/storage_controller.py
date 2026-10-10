"""Storage controller: manage NFS shares and their node/cluster assignment."""

from __future__ import annotations

from core.exceptions import NotFound, ValidationException
from core.security import ValidationError, Validator
from core.support import now_iso
from resources.app.models.storage import Storage
from resources.app.services.storage import StorageProbe
from resources.controllers.controller import Controller

RULES = {
    "storage_id": "required|storage_id",
    "server": "required|host_or_ip",
    "export": "required|export_path",
    "kind": "nullable|in:nfs",
}


class StorageController(Controller):
    def index(self, request):
        storages = Storage.query().order_by("storage_id").get()
        return self.render_page(
            "pages/storage/index.html",
            {"storages": storages, "active": "storage"},
            request,
        )

    def store(self, request):
        try:
            data = self.validate(request.form, RULES)
        except ValidationException as exc:
            self.flash(self._first_error(exc), "error")
            return self._done(request, "/storage")

        if Storage.where("storage_id", data["storage_id"]).exists():
            self.flash("A storage with that ID already exists.", "error")
            return self._done(request, "/storage")

        reachable, exports, message = StorageProbe.inspect(data["server"])
        Storage.create(
            {
                "storage_id": data["storage_id"],
                "kind": data.get("kind") or "nfs",
                "server": data["server"],
                "export": data["export"],
                "options": {"exports": exports},
                "reachable": 1 if reachable else 0,
                "last_checked_at": now_iso(),
            }
        )
        self.flash(
            f"Storage '{data['storage_id']}' created "
            f"({'reachable' if reachable else 'not reachable'}"
            f"{': ' + message if message else ''}).",
            "success" if reachable else "warning",
        )
        self._push_all()
        return self._done(request, "/storage")

    def check(self, request, storage_id: int):
        storage = self._find(storage_id)
        reachable, exports, message = StorageProbe.inspect(storage.server)
        storage.update(
            {
                "reachable": 1 if reachable else 0,
                "last_checked_at": now_iso(),
                "options": {"exports": exports},
            }
        )
        self.flash(
            f"'{storage.storage_id}': {'reachable' if reachable else 'unreachable'}"
            f"{' — ' + message if message else ''}.",
            "success" if reachable else "warning",
        )
        self._push_all()
        return self._done(request, "/storage")

    def destroy(self, request, storage_id: int):
        storage = self._find(storage_id)
        name = storage.storage_id
        storage.delete()
        self._push_all()
        self.flash(f"Storage '{name}' deleted.", "success")
        return self._done(request, "/storage")

    def exports(self, request):
        """HTMX endpoint: return available NFS exports for the given server."""
        raw = (request.args.get("server") or "").strip()
        exports: list[str] = []
        message = ""
        if not raw:
            message = "Enter a server first."
        else:
            try:
                server = Validator.ipv4(raw)
            except ValidationError:
                try:
                    server = Validator.hostname(raw)
                except ValidationError:
                    message = "Invalid server address."
                    server = ""
            if server:
                reachable, exports, message = StorageProbe.inspect(server)
                if not exports and reachable:
                    message = message or "No exports found."
        return self.render(
            "partials/storage_export_options.html",
            {"exports": exports, "message": message},
        )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _push_all() -> None:
        """Push fresh configs to every connected agent (storage change)."""
        from core.websocket import get_hub

        get_hub().notify_all()

    @staticmethod
    def _find(storage_id: int) -> Storage:
        storage = Storage.find(storage_id)
        if storage is None:
            raise NotFound("Storage not found.")
        return storage

    @staticmethod
    def _first_error(exc: ValidationException) -> str:
        for messages in exc.errors.values():
            if messages:
                return messages[0]
        return "Please check the input."
