"""Liveness/health endpoint for monitoring."""

from __future__ import annotations

from core.support import now_iso
from resources.controllers.controller import Controller


class HealthApiController(Controller):
    def index(self, request):
        return self.json({"status": "ok", "time": now_iso()})
