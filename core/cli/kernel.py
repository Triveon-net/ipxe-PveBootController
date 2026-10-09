"""CLI kernel: command registry, discovery, dispatch and interactive console.

Normal usage is simply::

    python main.py

This starts the HTTP server (in a background thread) and drops you into an
interactive console. From there you can run any command (``help`` lists them).
Type ``exit`` (or Ctrl+D) to stop the server and quit.

You can still run a single command non-interactively::

    python main.py app:user:make
    python main.py serve            # foreground server, no console
"""

from __future__ import annotations

import importlib
import pkgutil
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.cli import io

Handler = Callable[[object, list[str]], int | None]


@dataclass
class Command:
    name: str
    description: str
    handler: Handler


_REGISTRY: dict[str, Command] = {}
_discovered = False

META_COMMANDS = {
    "help": "Show this list of commands",
    "clear": "Clear the screen",
    "exit": "Stop the server and leave the console (also `quit`, Ctrl+D)",
}


def command(name: str, description: str = "") -> Callable[[Handler], Handler]:
    def decorator(func: Handler) -> Handler:
        _REGISTRY[name] = Command(name=name, description=description, handler=func)
        return func

    return decorator


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    if scripts_dir.is_dir():
        for module_info in pkgutil.iter_modules([str(scripts_dir)]):
            if module_info.name.startswith("_"):
                continue
            importlib.import_module(f"scripts.{module_info.name}")
    _discovered = True


def commands() -> dict[str, Command]:
    _discover()
    return dict(_REGISTRY)


def _print_help() -> None:
    io.title("Available commands")
    entries = sorted(_REGISTRY.values(), key=lambda c: c.name)
    width = max((len(c.name) for c in entries), default=10)
    for cmd in entries:
        io.line(f"  {cmd.name.ljust(width)}  {cmd.description}")
    io.line()
    meta_width = max(len(name) for name in META_COMMANDS)
    io.line(f"  {'help'.ljust(meta_width)}  {META_COMMANDS['help']}")
    io.line(f"  {'exit'.ljust(meta_width)}  {META_COMMANDS['exit']}")


def _run_command(app, cmd: Command, args: list[str]) -> int:
    """Run a command against an already-built application. Never raises."""
    try:
        result = cmd.handler(app, args)
    except KeyboardInterrupt:
        io.line()
        io.warn("Aborted.")
        return 130
    except Exception as exc:  # keep the console alive on command errors
        from config import get_config

        io.error(f"{type(exc).__name__}: {exc}")
        if get_config().debug:
            import traceback

            io.line(traceback.format_exc())
        return 1
    return int(result or 0)


def console() -> int:
    """Start the server and open the interactive console."""
    _discover()

    from config import get_config

    config = get_config()

    try:
        from core.application import Application

        app = Application(config)
        app.boot()
    except Exception as exc:
        io.error(f"Could not start the application: {exc}")
        return 1

    # Start the HTTP server alongside the console.
    server = None
    try:
        from core import server as server_mod

        server = server_mod.start_in_thread(config.host, config.port, app)
    except OSError as exc:
        io.warn(f"Could not bind {config.host}:{config.port} ({exc}). Commands only.")

    io.line()
    io.line(io._c(f"  {config.name}", "bold"))
    io.line(io._c("  interactive console", "dim"))
    io.line()
    if server is not None:
        io.success(f"Server running at {io._c(server.url, 'cyan')}")
    io.line(f"{io._c('env', 'dim')} {config.env}    {io._c('db', 'dim')} {config.database_path}")
    io.info("Type `help` for commands, `exit` to stop and quit.")

    while True:
        try:
            raw = input(io._c("app > ", "cyan"))
        except EOFError:
            io.line()
            break
        except KeyboardInterrupt:
            io.line()
            continue

        line = raw.strip()
        if not line:
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:
            io.error(f"Could not parse input: {exc}")
            continue
        if not parts:
            continue

        name, args = parts[0], parts[1:]

        if name in ("exit", "quit", "q"):
            break
        if name in ("help", "list", "?"):
            _print_help()
            continue
        if name == "clear":
            print("\033[2J\033[H", end="")
            continue

        cmd = _REGISTRY.get(name)
        if cmd is None:
            io.error(f"Unknown command: {name!r}. Type `help` for a list.")
            continue

        _run_command(app, cmd, args)
        io.line()

    from core import server as server_mod

    server_mod.stop_active()
    io.info("Server stopped. Goodbye.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _discover()

    # No arguments -> start the server and open the interactive console.
    if not argv:
        return console()

    name, args = argv[0], argv[1:]
    if name in ("help", "-h", "--help", "list"):
        _print_help()
        return 0

    cmd = _REGISTRY.get(name)
    if cmd is None:
        io.error(f"Unknown command: {name!r}")
        _print_help()
        return 1

    from config import get_config

    try:
        from core.application import Application

        app = Application(get_config())
    except Exception as exc:
        io.error(f"Could not start the application: {exc}")
        return 1
    return _run_command(app, cmd, args)
