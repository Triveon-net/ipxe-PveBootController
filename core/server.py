"""Manage the HTTP server running alongside the interactive console.

The console starts the WSGI server in a daemon thread so the operator keeps an
interactive prompt while the panel is served.
"""

from __future__ import annotations

import threading

from werkzeug.serving import BaseWSGIServer, make_server

from core.websocket import WebSocketRequestHandler

_active: "ServerHandle | None" = None


class ServerHandle:
    def __init__(self, host: str, port: int, app) -> None:
        self.host = host
        self.port = port
        self._server: BaseWSGIServer = make_server(
            host, port, app, threaded=True, request_handler=WebSocketRequestHandler
        )
        self._thread: threading.Thread | None = None

    def start(self) -> "ServerHandle":
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="pve-http", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except Exception:
            pass

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}"


def active_server() -> "ServerHandle | None":
    return _active


def start_in_thread(host: str, port: int, app) -> ServerHandle:
    """Start the server in a background thread and record it as active."""
    global _active
    handle = ServerHandle(host, port, app).start()
    _active = handle
    return handle


def stop_active() -> None:
    global _active
    if _active is not None:
        _active.stop()
        _active = None
