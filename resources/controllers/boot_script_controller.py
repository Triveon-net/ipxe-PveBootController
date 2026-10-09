"""Serves the iPXE boot script to booting machines.

The script registers the machine's MAC with the controller and chainloads a
provisioning image. The actual image is operator-provided (see the
``pxe.chain_url`` setting); this endpoint is the glue that points nodes at it.
"""

from __future__ import annotations

from core.response import text_response
from resources.app.models.setting import Setting
from resources.controllers.controller import Controller


class BootScriptController(Controller):
    def ipxe(self, request):
        base = self.config.url
        next_server = Setting.get("pxe.next_server") or self.config.pxe_next_server
        chain_url = Setting.get("pxe.chain_url") or ""

        lines = [
            "#!ipxe",
            "echo iPXE PVE Controller",
            f"set controller {base}",
            "echo Node MAC: ${net0/mac}",
            f"echo Report hardware to ${{controller}}/api/boot/report",
            f"set next-server {next_server}",
        ]
        if chain_url:
            lines.append(f"chain {chain_url}")
        else:
            lines.extend(
                [
                    "echo No provisioning image configured (pxe.chain_url).",
                    "echo Set it in the panel under Settings.",
                    "sleep 5",
                    "exit",
                ]
            )
        return text_response("\n".join(lines) + "\n")
