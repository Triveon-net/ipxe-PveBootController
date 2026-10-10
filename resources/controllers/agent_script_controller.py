"""Serves the provisioning agents that run inside the node image."""

from __future__ import annotations

from core.exceptions import NotFound
from core.response import text_response
from resources.controllers.controller import Controller

# filename -> content type
AGENTS = {
    "pve-report.sh": "text/x-shellscript; charset=utf-8",
    "pve-agent.py": "text/x-python; charset=utf-8",
    "pve-agent.service": "text/plain; charset=utf-8",
    "pve-agent.conf": "text/plain; charset=utf-8",
}


class AgentScriptController(Controller):
    def report(self, request):
        return self._serve(request, "pve-report.sh")

    def python(self, request):
        return self._serve(request, "pve-agent.py")

    def service(self, request):
        return self._serve(request, "pve-agent.service")

    def conf(self, request):
        return self._serve(request, "pve-agent.conf")

    def install(self, request):
        """Installer script, with this controller's URL injected."""
        path = self.config.resources_path / "agents" / "install.sh"
        if path.is_file():
            content = path.read_text(encoding="utf-8")
        else:
            content = "#!/bin/sh\necho 'install.sh is missing' >&2\nexit 1\n"
        content = self._substitute(request, content)
        response = text_response(content)
        response.headers["Content-Type"] = "text/x-shellscript; charset=utf-8"
        return response

    def _serve(self, request, filename: str):
        if filename not in AGENTS:
            raise NotFound("Unknown agent script.")
        path = self.config.resources_path / "agents" / filename
        if path.is_file():
            content = path.read_text(encoding="utf-8")
        else:
            content = f"# {filename} is missing on the controller\n"
        content = self._substitute(request, content)
        response = text_response(content)
        response.headers["Content-Type"] = AGENTS[filename]
        if request.args.get("download"):
            response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    def _substitute(self, request, content: str) -> str:
        """Fill in controller-derived placeholders (URLs the agent should use)."""
        base = f"{request.scheme}://{request.host}"
        ws_scheme = "wss" if request.scheme == "https" else "ws"
        ws_url = f"{ws_scheme}://{request.host}{self.config.ws_path}"
        return content.replace("__CONTROLLER__", base).replace("__WS_URL__", ws_url)
