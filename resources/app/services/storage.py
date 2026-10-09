"""Probe NFS servers for reachability and available exports.

Uses `showmount -e <server>` through the framework's safe command runner (no
shell, argument list, allow-listed binary, bounded timeout). If `showmount` is
not installed, falls back to a plain TCP connect on the NFS port so we can at
least report reachability.
"""

from __future__ import annotations

import socket
import subprocess

from core.security import CommandNotAllowed, Shell

_ALLOWED = frozenset({"showmount"})


class StorageProbe:
    TIMEOUT = 8
    NFS_PORT = 2049

    @classmethod
    def inspect(cls, server: str) -> tuple[bool, list[str], str]:
        """Return ``(reachable, exports, message)`` for an NFS server."""
        server = str(server).strip()
        try:
            result = Shell.run(
                ["showmount", "-e", server], allowed=_ALLOWED, timeout=cls.TIMEOUT
            )
        except FileNotFoundError:
            return cls._tcp_fallback(server, "'showmount' is not installed")
        except subprocess.TimeoutExpired:
            return False, [], "Timed out while contacting the server."
        except CommandNotAllowed:
            return False, [], "The 'showmount' command is not allowed."
        except Exception as exc:  # noqa: BLE001 - report, never crash the request
            return False, [], f"Probe failed: {exc}"

        exports = cls._parse_exports(result.stdout.decode("utf-8", errors="replace"))
        if exports:
            return True, exports, ""
        if result.returncode == 0:
            return True, [], "Reachable, but the server exports nothing."
        return cls._tcp_fallback(server, "showmount returned no exports")

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _parse_exports(output: str) -> list[str]:
        exports: list[str] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("export list"):
                continue
            path = line.split()[0]
            if path.startswith("/") and path not in exports:
                exports.append(path)
        return exports

    @classmethod
    def _tcp_fallback(cls, server: str, note: str) -> tuple[bool, list[str], str]:
        try:
            with socket.create_connection((server, cls.NFS_PORT), timeout=3):
                return True, [], f"{note} (NFS port {cls.NFS_PORT} is reachable)."
        except OSError:
            return False, [], note
