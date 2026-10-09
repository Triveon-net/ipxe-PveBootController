"""Application commands: serve, key generation, route info."""

from __future__ import annotations

import base64
import secrets

from config import project_root
from core.cli import io
from core.cli.kernel import command


@command("serve", "Start the web server in the foreground (no console)")
def serve(app, args) -> int:
    from core import server as server_mod
    from werkzeug.serving import run_simple

    if server_mod.active_server() is not None:
        io.info(f"Server already running at {server_mod.active_server().url}")
        return 0

    host, port = app.config.host, app.config.port
    # Optional overrides: `serve --host 0.0.0.0 --port 8080`
    i = 0
    while i < len(args):
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            i += 1

    app.boot()
    io.title(f"{app.config.name}")
    io.info(f"Environment : {app.config.env}")
    io.info(f"Database    : {app.config.database_path}")
    io.info(f"Listening   : http://{host}:{port}")
    io.line()
    try:
        run_simple(host, port, app, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        io.line()
        io.warn("Server stopped.")
    return 0


@command("key:generate", "Generate a new APP_KEY and store it in .env")
def key_generate(app, args) -> int:
    root = project_root()
    env_path = root / ".env"
    example_path = root / ".env.example"

    if not env_path.exists():
        if example_path.exists():
            env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
            io.info("Created .env from .env.example")
        else:
            env_path.write_text("", encoding="utf-8")

    key = "base64:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    output: list[str] = []
    for raw_line in lines:
        if raw_line.strip().startswith("APP_KEY="):
            output.append(f"APP_KEY={key}")
            replaced = True
        else:
            output.append(raw_line)
    if not replaced:
        output.append(f"APP_KEY={key}")
    env_path.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8")

    io.success("Application key generated and written to .env.")
    io.info("Restart the server for the change to take effect.")
    return 0


@command("route:list", "List all registered routes")
def route_list(app, args) -> int:
    app.boot()
    io.title("Registered routes")
    rows = []
    for route in app.router.routes():
        handler = route.handler if isinstance(route.handler, str) else "<callable>"
        name = route.name or ""
        group = "api" if route.is_api else "web"
        rows.append((", ".join(route.methods), route.path, handler, name, group))
    rows.sort(key=lambda r: r[1])
    io.table(["Methods", "Path", "Handler", "Name", "Group"], rows)
    return 0


@command("about", "Show environment information")
def about(app, args) -> int:
    import platform
    import sys

    io.title("About")
    io.info(f"Application : {app.config.name}")
    io.info(f"Environment : {app.config.env}")
    io.info(f"Python      : {sys.version.split()[0]} ({platform.system()})")
    io.info(f"Project root: {app.config.root}")
    io.info(f"Database    : {app.config.database_path}")
    io.info(f"App key set : {'yes' if app.config.key else 'no'}")
    return 0
