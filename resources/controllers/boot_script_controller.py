"""Serves the iPXE boot script to booting machines.

The script is generated from the editable options in the **Boot** tab
(`pxe.boot_config`); the node's provisioning image is configured there too.
"""

from __future__ import annotations

from core.response import text_response
from resources.app.services.boot_config import BootConfig
from resources.controllers.controller import Controller


class BootScriptController(Controller):
    def ipxe(self, request):
        return text_response(BootConfig.render(BootConfig.load()))
