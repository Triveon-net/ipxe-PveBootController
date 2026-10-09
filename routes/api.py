"""Route definitions (machine/API).

These routes are consumed by booting nodes and other automation, so they use
the `api_token` guard instead of sessions/CSRF. Input is still fully validated
and all database access is parameterised by the framework.
"""

from __future__ import annotations


def register(router) -> None:
    with router.group(prefix="/api", middleware=("api_token",), is_api=True):
        router.get("/health", "Api/HealthApiController@index").name("api.health")
        router.post("/boot/report", "Api/BootApiController@report").name("api.boot.report")
        router.get("/boot/config/<string:mac>", "Api/BootApiController@fetch").name(
            "api.boot.config"
        )
        router.get("/nodes", "Api/NodesApiController@index").name("api.nodes.index")

    # iPXE boot script (served to booting machines, no token required).
    with router.group(is_api=True):
        router.get("/boot.ipxe", "BootScriptController@ipxe").name("boot.ipxe")
        router.get("/agent/pve-report.sh", "AgentScriptController@report").name("agent.report")
        router.get("/agent/pve-agent.py", "AgentScriptController@python").name("agent.python")
        router.get("/agent/pve-agent.service", "AgentScriptController@service").name(
            "agent.service"
        )
        router.get("/agent/pve-agent.conf", "AgentScriptController@conf").name("agent.conf")
        router.get("/agent/install.sh", "AgentScriptController@install").name("agent.install")
        router.get("/bootfiles/<path:name>", "BootFilesController@serve").name("bootfiles.serve")
