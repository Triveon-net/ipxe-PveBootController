"""Store, hash and serve uploaded boot files (kernel, initrd, squashfs, ...)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from config import get_config
from core.support import now_iso
from resources.app.models.boot_file import BootFile

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_CHUNK = 1024 * 1024


class InvalidBootFile(ValueError):
    pass


class BootFiles:
    @classmethod
    def sanitize_name(cls, filename: str | None) -> str:
        # Strip any directory components a browser might send.
        name = (filename or "").replace("\\", "/").split("/")[-1].strip()
        if not _NAME_RE.match(name):
            raise InvalidBootFile(
                "Invalid file name. Use letters, digits, dot, dash, underscore, plus."
            )
        return name

    @classmethod
    def path_for(cls, name: str) -> Path:
        if not _NAME_RE.match(name):
            raise InvalidBootFile("Invalid file name.")
        return get_config().bootfiles_path / name

    @classmethod
    def save(cls, file_storage, *, original_name: str | None = None) -> BootFile:
        name = cls.sanitize_name(original_name or file_storage.filename)
        directory = get_config().bootfiles_path
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name

        limit = get_config().bootfile_max_bytes
        digest = hashlib.sha256()
        size = 0
        file_storage.stream.seek(0)
        with open(target, "wb") as handle:
            while True:
                chunk = file_storage.stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise InvalidBootFile(
                        f"File exceeds the limit of {limit // (1024 * 1024)} MB."
                    )
                digest.update(chunk)
                handle.write(chunk)

        return cls._record(
            name,
            size=size,
            sha256=digest.hexdigest(),
            content_type=file_storage.mimetype or "application/octet-stream",
            original_name=file_storage.filename or name,
        )

    @classmethod
    def store_text(
        cls,
        name: str,
        content: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
        original_name: str | None = None,
    ) -> BootFile:
        """Write generated text (e.g. the iPXE script) as a boot file.

        Used to materialise the rendered `boot.ipxe` on disk so a static file
        server / DHCP option 67 always serves the current script.
        """
        name = cls.sanitize_name(name)
        directory = get_config().bootfiles_path
        directory.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        (directory / name).write_bytes(data)
        return cls._record(
            name,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            original_name=original_name or name,
        )

    @classmethod
    def _record(
        cls,
        name: str,
        *,
        size: int,
        sha256: str,
        content_type: str,
        original_name: str,
    ) -> BootFile:
        payload = {
            "original_name": original_name,
            "size": size,
            "sha256": sha256,
            "content_type": content_type,
            "created_at": now_iso(),
        }
        existing = BootFile.find_by_name(name)
        if existing is not None:
            existing.update(payload)
            return existing
        return BootFile.create({"name": name, **payload})

    @classmethod
    def delete(cls, boot_file: BootFile) -> None:
        try:
            cls.path_for(boot_file.name).unlink(missing_ok=True)
        except (OSError, InvalidBootFile):
            pass
        boot_file.delete()
