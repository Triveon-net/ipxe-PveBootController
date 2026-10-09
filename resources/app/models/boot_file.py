"""Uploaded boot files (kernel, initrd, squashfs, ...)."""

from __future__ import annotations

from core.model import Model


class BootFile(Model):
    table = "boot_files"

    @classmethod
    def _timestamps(cls) -> bool:
        return False

    @classmethod
    def find_by_name(cls, name: str) -> "BootFile | None":
        return cls.where("name", name).first()

    @property
    def size_human(self) -> str:
        size = float(self._attributes.get("size") or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
