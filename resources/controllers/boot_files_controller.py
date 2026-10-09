"""Upload, delete and serve boot files."""

from __future__ import annotations

from werkzeug.utils import send_file

from core.exceptions import NotFound
from resources.app.models.boot_file import BootFile
from resources.app.services.boot_files import BootFiles, InvalidBootFile
from resources.controllers.controller import Controller, is_htmx


class BootFilesController(Controller):
    # -- upload / delete (panel, requires auth + CSRF) ----------------------
    def store(self, request):
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            self.flash("Please choose a file to upload.", "error")
            return self._done(request, "/boot")

        name = request.form.get("name", "").strip()
        try:
            boot_file = BootFiles.save(upload, original_name=name or None)
        except InvalidBootFile as exc:
            self.flash(str(exc), "error")
            return self._done(request, "/boot")

        self.flash(f"Uploaded '{boot_file.name}' ({boot_file.size_human}).", "success")
        return self._done(request, "/boot")

    def destroy(self, request, file_id: int):
        boot_file = BootFile.find(file_id)
        if boot_file is None:
            raise NotFound("File not found.")
        name = boot_file.name
        BootFiles.delete(boot_file)
        self.flash(f"Deleted '{name}'.", "success")
        return self._done(request, "/boot")

    # -- serve (public, fetched by booting machines) ------------------------
    def serve(self, request, name: str):
        boot_file = BootFile.find_by_name(name)
        if boot_file is None:
            raise NotFound("File not found.")
        path = BootFiles.path_for(name)
        if not path.is_file():
            raise NotFound("File is missing on disk.")
        return send_file(
            path,
            request.environ,
            mimetype=boot_file.content_type or "application/octet-stream",
            conditional=True,  # supports HTTP Range (useful for big images)
            download_name=name,
        )

    def _done(self, request, url: str):
        if is_htmx(request):
            return self.htmx_redirect(url)
        return self.redirect(url)
