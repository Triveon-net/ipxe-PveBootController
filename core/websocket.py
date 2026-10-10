"""WebSocket hub served on the **same port** as HTTP.

Werkzeug's WSGI server cannot upgrade a connection, so a request that carries
``Upgrade: websocket`` is taken over in `WebSocketRequestHandler.run_wsgi`
*before* it reaches the WSGI app: the RFC 6455 handshake is completed on the raw
socket and the connection then becomes a persistent agent channel.

This means no second port and no extra dependency - the framing is implemented
here with the standard library, matching the client in
``resources/agents/pve-agent.py`` (text messages only, like the client).

The agent always initiates the connection; the panel only ever sends on sockets
that agents already opened, so nodes behind NAT keep working.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import socket
import struct
import threading
import time
import urllib.parse
from collections.abc import Iterable

from werkzeug.serving import WSGIRequestHandler

from core.support import now_iso

log = logging.getLogger("pve.ws")

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_PING_EVERY = 20  # seconds between server keepalive pings
_DEAD_AFTER = 45  # seconds without any frame -> drop the connection


def _clean_mac(value: object) -> str:
    return str(value or "").strip().lower()


def _store_vms_for(mac: str, items: object, prune: bool = True) -> None:
    """Persist the VM set a node reported (keyed by node + vmid).

    ``prune`` is False while a node is still within its boot grace window: the
    report is merged (add/update only) so a freshly reset node whose
    `/etc/pve/qemu-server` is still empty cannot wipe the stored VMs before the
    restore config has been pushed.
    """
    if not isinstance(items, list):
        return
    from resources.app.models.node import Node
    from resources.app.models.vm import Vm

    node = Node.find_by_mac(mac)
    if node is None:
        return
    if prune:
        Vm.replace_for_node(node.id, node.cluster_id, items[:5000])
    else:
        Vm.upsert_for_node(node.id, node.cluster_id, items[:5000])


class WebSocketClosed(Exception):
    pass


class _SocketIO:
    """RFC 6455 framing over an established socket (server side, text only)."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._buffer = b""
        self._send_lock = threading.Lock()

    # -- receive ------------------------------------------------------------
    def read_frame(self) -> tuple[int, bytes]:
        byte1, byte2 = self._recv_exact(2)
        opcode = byte1 & 0x0F
        masked = bool(byte2 & 0x80)
        length = byte2 & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._recv_exact(8))
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketClosed("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    # -- send ---------------------------------------------------------------
    def send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        with self._send_lock:
            self.sock.sendall(bytes(header) + payload)

    def send_text(self, text: str) -> None:
        self.send_frame(0x1, text.encode("utf-8"))


class AgentConnection:
    def __init__(self, sock: socket.socket) -> None:
        self._io = _SocketIO(sock)
        self.mac = ""
        self.connected_at = now_iso()
        self.last_seen = now_iso()
        self.hardware: dict = {}
        self.last_rx = time.monotonic()
        self._stop = threading.Event()

    def send(self, payload: dict) -> None:
        self._io.send_text(json.dumps(payload))

    def close(self) -> None:
        self._stop.set()
        try:
            self._io.send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._io.sock.close()
        except OSError:
            pass


class AgentHub:
    """Thread-safe registry of connected agents plus server -> agent pushes."""

    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}
        self._metrics: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- registry -----------------------------------------------------------
    def connected_macs(self) -> set[str]:
        with self._lock:
            return set(self._connections.keys())

    def is_connected(self, mac: str) -> bool:
        with self._lock:
            return _clean_mac(mac) in self._connections

    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def _register(self, conn: AgentConnection) -> None:
        with self._lock:
            old = self._connections.get(conn.mac)
            self._connections[conn.mac] = conn
        if old is not None and old is not conn:
            old.close()  # a reconnecting node replaces its previous socket

    def _unregister(self, conn: AgentConnection) -> None:
        with self._lock:
            if self._connections.get(conn.mac) is conn:
                self._connections.pop(conn.mac, None)
                self._metrics.pop(conn.mac, None)

    # -- live metrics -------------------------------------------------------
    def update_metrics(self, mac: str, data: dict) -> None:
        mac = _clean_mac(mac)
        with self._lock:
            self._metrics[mac] = data

    def metrics(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._metrics)

    # -- server -> agent pushes --------------------------------------------
    def notify_macs(self, macs: Iterable[str]) -> None:
        with self._lock:
            conns = [self._connections.get(_clean_mac(m)) for m in macs]
        for conn in conns:
            if conn is not None:
                self._push_state(conn)

    def notify_all(self) -> None:
        with self._lock:
            conns = list(self._connections.values())
        for conn in conns:
            self._push_state(conn)

    def _push_state(self, conn: AgentConnection) -> None:
        from resources.app.services.boot_report import BootReport

        try:
            conn.send(_to_message(BootReport.state(conn.mac)))
        except Exception:
            pass

    # -- per-connection loop (runs in the handler's thread) -----------------
    def handle_connection(self, conn: AgentConnection) -> None:
        from resources.app.services.boot_report import BootReport

        conn._io.sock.settimeout(None)  # blocking reads; pings come from the heartbeat
        threading.Thread(target=self._heartbeat, args=(conn,), daemon=True).start()
        try:
            while True:
                try:
                    opcode, payload = conn._io.read_frame()
                except (OSError, WebSocketClosed):
                    break

                conn.last_rx = time.monotonic()
                conn.last_seen = now_iso()
                if opcode == 0x1:  # text
                    self._on_text(conn, payload, BootReport)
                elif opcode == 0x8:  # close
                    break
                elif opcode == 0x9:  # ping -> pong
                    try:
                        conn._io.send_frame(0xA, payload)
                    except OSError:
                        break
                # 0xA (pong) and 0x0 (continuation) are ignored
        finally:
            conn._stop.set()
            if conn.mac:
                self._unregister(conn)
                log.info("agent disconnected: %s", conn.mac)
            conn.close()
            _close_db()

    def _heartbeat(self, conn: AgentConnection) -> None:
        """Ping an idle agent; drop it if nothing comes back for a while."""
        while not conn._stop.wait(_PING_EVERY):
            try:
                conn._io.send_frame(0x9, b"")
            except OSError:
                return
            if time.monotonic() - conn.last_rx > _DEAD_AFTER:
                log.info("agent %s unresponsive; closing", conn.mac or "?")
                conn.close()
                return

    def _on_text(self, conn: AgentConnection, payload: bytes, boot_report) -> None:
        try:
            message = json.loads(payload.decode("utf-8", "replace"))
        except (TypeError, ValueError):
            return
        if not isinstance(message, dict):
            return

        kind = str(message.get("type") or "")

        if kind == "hello":
            mac = _clean_mac(message.get("mac_address") or message.get("mac"))
            if not mac:
                conn.send({"type": "error", "message": "missing mac_address"})
                return
            conn.mac = mac
            conn.hardware = boot_report.hardware_from(message)
            # No config in the handshake reply: the agent reports its VM set next
            # (before requesting the full config), so the DB is fresh by then.
            conn.send(_to_message(boot_report.record(mac, conn.hardware), include_config=False))
            self._register(conn)
            log.info("agent connected: %s", mac)

        elif kind == "report":
            if not conn.mac:
                return
            conn.hardware = boot_report.hardware_from(message.get("hardware") or message)
            conn.send(_to_message(boot_report.record(conn.mac, conn.hardware)))

        elif kind == "sync":
            # The agent asks for everything it can get on (re)connect.
            if conn.mac:
                conn.send(_to_message(boot_report.state(conn.mac)))

        elif kind == "vms":
            if not conn.mac:
                return
            conn.last_seen = now_iso()
            # Agents send prune=False during their boot grace window.
            self._store_vms(conn.mac, message.get("vms"), bool(message.get("prune", True)))

        elif kind == "status":
            log.info(
                "agent %s status: %s %s",
                conn.mac or "?",
                message.get("state") or "",
                message.get("message") or "",
            )

        elif kind == "metrics":
            if not conn.mac:
                return
            conn.last_seen = now_iso()
            self.update_metrics(conn.mac, _clean_metrics(message))

        elif kind == "ping":
            conn.send({"type": "pong"})

    def _store_vms(self, mac: str, items: object, prune: bool = True) -> None:
        _store_vms_for(mac, items, prune)


def _close_db() -> None:
    try:
        from database import get_db

        get_db().close()
    except Exception:
        pass


def _num(value: object, low: float = 0.0, high: float = 1e9) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(low, min(high, number))


def _clean_metrics(message: dict) -> dict:
    """Clamp agent-reported values so a bad payload cannot skew the panel."""
    return {
        "cpu_percent": round(_num(message.get("cpu_percent"), 0.0, 100.0), 1),
        "ram_percent": round(_num(message.get("ram_percent"), 0.0, 100.0), 1),
        "ram_used_gb": round(_num(message.get("ram_used_gb")), 1),
        "ram_total_gb": round(_num(message.get("ram_total_gb")), 1),
        "at": now_iso(),
    }


def _to_message(result: dict, include_config: bool = True) -> dict:
    """Translate a BootReport result into an agent message.

    ``include_config`` is disabled for the handshake reply: the agent reports its
    VM set right after ``hello``, so deferring the config until then guarantees
    deleted VMs are already dropped from the DB before the restore payload is
    built (otherwise the agent would recreate them on boot).
    """
    status = result.get("status")
    if status == "rejected":
        return {"type": "rejected", "message": result.get("message") or "rejected"}
    if status == "pending":
        return {
            "type": "pending",
            "message": result.get("message") or "not registered",
            "mac_address": result.get("mac_address"),
        }
    if not include_config:
        return {"type": "status", "state": "registered"}
    return {"type": "config", "node": result.get("node"), "config": result.get("config")}


def _authorised(handler: WSGIRequestHandler) -> bool:
    from resources.app.models.setting import Setting

    expected = Setting.get("api.token") or ""
    if not expected:
        return True

    provided = handler.headers.get("X-API-Token") or ""
    if not provided:
        query = urllib.parse.urlsplit(handler.path).query
        for part in query.split("&"):
            if part.startswith("token="):
                provided = urllib.parse.unquote(part[len("token="):])
                break
    return bool(provided) and hmac.compare_digest(provided, expected)


class WebSocketRequestHandler(WSGIRequestHandler):
    """Werkzeug request handler that upgrades WebSocket connections in place."""

    def run_wsgi(self) -> None:
        if self._is_upgrade():
            self._handle_upgrade()
            return
        super().run_wsgi()

    def _is_upgrade(self) -> bool:
        if (self.command or "").upper() != "GET":
            return False
        upgrade = (self.headers.get("Upgrade") or "").lower().strip()
        connection = (self.headers.get("Connection") or "").lower()
        return upgrade == "websocket" and "upgrade" in connection

    def _handle_upgrade(self) -> None:
        from config import get_config

        self.close_connection = True  # never fall back into the HTTP keep-alive loop

        path = urllib.parse.urlsplit(self.path).path
        key = self.headers.get("Sec-WebSocket-Key") or ""
        version = (self.headers.get("Sec-WebSocket-Version") or "").strip()

        if path != get_config().ws_path or not key or version != "13":
            self._plain_response(400, "Bad Request")
            return
        if not _authorised(self):
            self._plain_response(401, "Unauthorized")
            return

        accept = base64.b64encode(
            hashlib.sha1((key + _GUID).encode("ascii")).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        try:
            self.connection.sendall(response.encode("ascii"))
        except OSError:
            return

        get_hub().handle_connection(AgentConnection(self.connection))

    def _plain_response(self, code: int, message: str) -> None:
        try:
            self.connection.sendall(
                f"HTTP/1.1 {code} {message}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode(
                    "ascii"
                )
            )
        except OSError:
            pass


# -- module singleton --------------------------------------------------------
_hub: AgentHub | None = None


def get_hub() -> AgentHub:
    global _hub
    if _hub is None:
        _hub = AgentHub()
    return _hub
