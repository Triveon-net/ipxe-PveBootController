"""iPXE-PveController provisioning agent (runs inside the node image).

Reports the node's hardware to the controller and applies the returned
configuration: hostname, management network, Corosync/cluster and NFS storages,
then regenerates the PVE certificates and restarts the PVE daemons. Standard
library only - no dependencies.

Settings come from a `pve-agent.conf` file (see `load_conf_file`), the
environment or the kernel command line. Command-line arguments win.

By default the agent runs permanently: it keeps reporting until the node is
accepted in the panel and then keeps the configuration applied. Pass `--once`
to report a single time and exit.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "3.1"

_VIRTUAL_RE = re.compile(
    r"^(lo|docker\d*|veth.*|br-[0-9a-f]+|virbr\d*|vnet\d+|tap\d+|tun\d+|wg\d+|cni\d+|flannel.*|cali.*|vmbr\d*)$"
)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def log(message: str) -> None:
    print(f"pve-agent: {message}", flush=True)


def host_from_url(url: str) -> str:
    host = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", url or "")
    host = host.split("/", 1)[0]
    return host.rsplit(":", 1)[0] if ":" in host else host


def derive_ws_url(controller: str, path: str = "/agent") -> str:
    """WebSocket URL for the controller (same host/port as HTTP)."""
    if not controller:
        return ""
    parts = urllib.parse.urlsplit(controller if "://" in controller else f"http://{controller}")
    netloc = parts.netloc
    if not netloc:
        return ""
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{netloc}{path}"


def run(args: list[str]) -> subprocess.CompletedProcess:
    """Run a command safely (argument list, never a shell)."""
    return subprocess.run(args, capture_output=True, text=True, check=False)


def is_virtual(name: str) -> bool:
    return bool(_VIRTUAL_RE.match(name))


def is_bridge(name: str) -> bool:
    """True if ``name`` is a Linux bridge (e.g. Proxmox ``vmbr0``).

    A bridge is the *virtual* interface the host IP sits on; the physical NIC is
    its port. Picking a bridge here is what produced `bridge-ports vmbr0`.
    """
    if name.startswith("vmbr"):
        return True
    return Path(f"/sys/class/net/{name}/bridge").is_dir()


def is_usable_nic(name: str) -> bool:
    """A physical NIC that can be a bridge port (not virtual, not a bridge)."""
    return bool(name) and not is_virtual(name) and not is_bridge(name)


def prefix_length(netmask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen


def replace_hostname(text: str, old: str, new: str) -> str:
    """Replace a hostname in config files (also inside FQDNs like name.domain)."""
    if not old or old == new:
        return text
    return re.sub(rf"(?<![A-Za-z0-9-]){re.escape(old)}(?![A-Za-z0-9-])", new, text)


def _looks_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Hardware / NIC detection
# --------------------------------------------------------------------------
def read_cmdline() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        raw = Path("/proc/cmdline").read_text()
    except OSError:
        return values
    for token in raw.split():
        if "=" in token:
            key, _, value = token.partition("=")
            if key in ("pve_ctrl", "pve_api_token", "pve_iface", "pve_mac"):
                values.setdefault(key[4:], value)
    return values


def _route_dev(target: str | None) -> str | None:
    """Device from the routing table (may be a bridge like vmbr0)."""
    commands = []
    if target:
        commands.append(["ip", "route", "get", target])
    commands.append(["ip", "route", "show", "default"])
    for cmd in commands:
        result = run(cmd)
        if result.returncode != 0:
            continue
        match = re.search(r"\bdev\s+(\S+)", result.stdout)
        if match:
            return match.group(1)
    return None


def bridge_ports(bridge: str) -> list[str]:
    """Physical NICs enslaved to a bridge (e.g. vmbr0 -> enp2s0)."""
    directory = Path(f"/sys/class/net/{bridge}/brif")
    if not directory.is_dir():
        return []
    try:
        return sorted(path.name for path in directory.iterdir())
    except OSError:
        return []


def detect_interface(controller: str) -> str | None:
    """The physical NIC that reaches the controller.

    If the route goes via a bridge (the usual Proxmox setup where the host IP
    sits on `vmbr0`), the *port* of that bridge is the NIC we want — not the
    bridge itself (which produced `bridge-ports vmbr0`).
    """
    dev = _route_dev(host_from_url(controller))
    if dev:
        if is_bridge(dev):
            for port in bridge_ports(dev):
                if is_usable_nic(port):
                    return port
        elif is_usable_nic(dev):
            return dev

    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return None

    up_with_ip: list[str] = []
    up: list[str] = []
    other: list[str] = []
    for path in sorted(net_dir.iterdir()):
        name = path.name
        if not is_usable_nic(name):
            continue
        state = path.joinpath("operstate")
        try:
            up_state = not (state.is_file() and state.read_text().strip() == "down")
        except OSError:
            up_state = True
        bucket = (up_with_ip if up_state else other) if interface_ip(name) else (up if up_state else other)
        bucket.append(name)

    for bucket in (up_with_ip, up, other):
        if bucket:
            return bucket[0]
    return None


def list_interfaces() -> str:
    """Human-readable interface census, for diagnosing detection failures."""
    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return "(no /sys/class/net)"
    parts: list[str] = []
    try:
        entries = sorted(net_dir.iterdir())
    except OSError:
        return "(unreadable)"
    for path in entries:
        name = path.name
        try:
            state = path.joinpath("operstate").read_text().strip()
        except OSError:
            state = "?"
        if is_bridge(name):
            kind = "bridge"
        elif is_virtual(name):
            kind = "virtual"
        else:
            kind = "nic"
        parts.append(f"{name}({kind},{state})")
    return ", ".join(parts) or "(none)"


def interface_mac(name: str) -> str:
    path = Path(f"/sys/class/net/{name}/address")
    try:
        return path.read_text().strip().lower()
    except OSError:
        return ""


def interface_ip(name: str) -> str:
    result = run(["ip", "-4", "addr", "show", "dev", name])
    if result.returncode == 0:
        match = re.search(r"inet\s+([0-9.]+)", result.stdout)
        if match:
            return match.group(1)
    return ""


def collect_hardware(iface: str | None, mac: str) -> dict:
    cpu_model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("model name", "hardware")):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    cpu_count = os.cpu_count() or 0
    ram_gb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) // 1024 // 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    return {
        "mac_address": mac,
        "cpu_model": cpu_model,
        "cpu_count": cpu_count,
        "ram_gb": ram_gb,
        "ip_address": interface_ip(iface) if iface else "",
        "nic": iface or "",
    }


# --------------------------------------------------------------------------
# Live resource usage (CPU / RAM, reported every few seconds)
# --------------------------------------------------------------------------
def _read_cpu_times() -> tuple[int, int] | None:
    """Return ``(idle, total)`` jiffies from /proc/stat, or None."""
    try:
        with open("/proc/stat") as handle:
            parts = handle.readline().split()
    except OSError:
        return None
    try:
        values = [int(v) for v in parts[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    return idle, sum(values)


def read_ram() -> tuple[float, float, float]:
    """Return ``(used_gb, total_gb, percent)`` from /proc/meminfo."""
    total_kb = available_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available_kb = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    if total_kb <= 0:
        return 0.0, 0.0, 0.0
    used_kb = max(0, total_kb - available_kb)
    return (
        round(used_kb / 1024 / 1024, 1),
        round(total_kb / 1024 / 1024, 1),
        round(used_kb / total_kb * 100, 1),
    )


class ResourceSampler:
    """CPU usage from /proc/stat deltas, RAM from /proc/meminfo."""

    def __init__(self) -> None:
        self._last = _read_cpu_times()

    def sample(self) -> dict:
        used_gb, total_gb, ram_percent = read_ram()
        cpu_percent = 0.0
        current = _read_cpu_times()
        if current is not None and self._last is not None:
            d_idle = current[0] - self._last[0]
            d_total = current[1] - self._last[1]
            if d_total > 0:
                cpu_percent = round(max(0.0, min(100.0, (1 - d_idle / d_total) * 100)), 1)
        self._last = current
        return {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": used_gb,
            "ram_total_gb": total_gb,
        }


# --------------------------------------------------------------------------
# VM configs (pull from the node, restore when missing)
# --------------------------------------------------------------------------
def _vm_name_from_config(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def default_vm_bases() -> list[str]:
    """Directories that hold the local node's VM configs."""
    bases = ["/etc/pve/qemu-server"]
    try:
        hostname = Path("/etc/hostname").read_text().strip()
    except OSError:
        hostname = ""
    if hostname:
        bases.append(f"/etc/pve/nodes/{hostname}/qemu-server")
    return bases


def collect_vms(bases: list[str] | None = None) -> list[dict]:
    """Read VM configs for the local node.

    Standalone: `/etc/pve/qemu-server/<vmid>.conf`. In a cluster that path only
    exposes the local node's VMs; `/etc/pve/nodes/<node>/qemu-server/` is scanned
    too and de-duplicated by VMID.
    """
    if bases is None:
        bases = default_vm_bases()

    vms: dict[int, dict] = {}
    for base in bases:
        directory = Path(base)
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.glob("*.conf"))
        except OSError:
            continue
        for path in entries:
            if not path.stem.isdigit():
                continue
            vmid = int(path.stem)
            if vmid in vms:
                continue
            try:
                config = path.read_text()
            except OSError:
                continue
            vms[vmid] = {"vmid": vmid, "name": _vm_name_from_config(config), "config": config}
    return list(vms.values())


def vms_signature(vms: list[dict]) -> str:
    """Stable hash of a VM set (vmid + config), to detect changes."""
    payload = json.dumps(sorted(vms, key=lambda v: v.get("vmid", 0)), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Removals are only reported to the panel once the agent's startup has completed
# cleanly (see ``s["startup_done"]``), never during the boot window where
# ``/etc/pve/qemu-server`` is still empty.


def vms_message(vms: list[dict], prune: bool) -> dict:
    """Build a VM report.

    ``prune`` lets the panel delete VMs the node no longer has. It stays off
    until the agent's startup has completed cleanly, so a freshly reset node
    whose ``/etc/pve/qemu-server`` is still empty cannot wipe the stored VMs
    before the restore config has been applied.
    """
    return {"type": "vms", "vms": vms, "prune": bool(prune)}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def report_hardware(controller: str, token: str | None, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{controller.rstrip('/')}/api/boot/report", data=data, method="POST"
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"controller returned HTTP {exc.code}: {body}") from exc


# --------------------------------------------------------------------------
# WebSocket client (RFC 6455, standard library only)
# --------------------------------------------------------------------------
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClosed(Exception):
    pass


class WebSocket:
    """Minimal text-only WebSocket client (masked client frames, no extensions)."""

    def __init__(self, url: str, token: str | None = None, timeout: int = 20) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buffer = b""

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> None:
        parts = urllib.parse.urlsplit(self.url)
        host = parts.hostname or ""
        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        raw = socket.create_connection((host, port), timeout=self.timeout)
        if secure:
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self._buffer = b""

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if self.token:
            lines.append(f"X-API-Token: {self.token}")
        raw.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        header = self._read_until(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError(f"handshake failed: {status.decode('latin-1', 'replace')}")
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if expected.lower().encode() not in header.lower():
            raise RuntimeError("handshake failed: bad Sec-WebSocket-Accept")

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None

    # -- sending ------------------------------------------------------------
    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.sock is None:
            raise WebSocketClosed("not connected")
        header = bytearray([0x80 | opcode])
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    # -- receiving ----------------------------------------------------------
    def recv_text(self, timeout: float | None = None) -> str | None:
        """Return the next text message, or None when the peer closed.

        Raises `socket.timeout` when idle (no data within `timeout`).
        """
        while True:
            frame = self._read_frame(timeout)
            opcode, payload = frame
            if opcode == 0x1:  # text
                return payload.decode("utf-8", "replace")
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping -> pong
                self._send_frame(0xA, payload)
            # 0xA pong and 0x0 continuation are ignored

    def _read_frame(self, timeout: float | None) -> tuple[int, bytes]:
        if self.sock is None:
            raise WebSocketClosed("not connected")
        self.sock.settimeout(timeout)
        first = self._recv_exact(2)  # socket.timeout here means "idle", safe
        byte1, byte2 = first[0], first[1]
        opcode = byte1 & 0x0F
        masked = bool(byte2 & 0x80)
        length = byte2 & 0x7F
        self.sock.settimeout(self.timeout)  # rest of the frame is blocking
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(4096)  # type: ignore[union-attr]
            if not chunk:
                raise WebSocketClosed("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self.sock.recv(4096)  # type: ignore[union-attr]
            if not chunk:
                raise WebSocketClosed("connection closed during handshake")
            self._buffer += chunk
        end = self._buffer.index(marker) + len(marker)
        data, self._buffer = self._buffer[:end], self._buffer[end:]
        return data


# --------------------------------------------------------------------------
# File generation (pure functions - unit tested)
# --------------------------------------------------------------------------
def _config_text(value) -> str:
    """Return usable raw config text; ignore empty or legacy-JSON values.

    Guards against a legacy `network_config`/`storage_config` of `{}` / `[]`
    (from before these became free text) being appended verbatim to a config
    file.
    """
    text = value if isinstance(value, str) else ""
    text = text.strip()
    return "" if not text or text[0] in "{[" else text


def render_interfaces(nic: str | None, cfg: dict) -> str:
    bridge = str(cfg.get("bridge") or "").strip() or "vmbr0"
    # The bridge port must be the physical NIC: operator-configured `bridge_port`
    # wins, otherwise the NIC the agent detected (never the bridge itself).
    phys = str(cfg.get("bridge_port") or "").strip() or str(nic or "").strip()
    ip = cfg.get("ip_address") or ""
    netmask = cfg.get("netmask") or "255.255.255.0"
    gateway = cfg.get("gateway") or ""
    vlan = cfg.get("vlan")

    lines = ["auto lo", "iface lo inet loopback", ""]
    if phys:
        lines += [f"iface {phys} inet manual", ""]

    port = phys
    if vlan and phys:
        port = f"{phys}.{vlan}"
        lines += [f"auto {port}", f"iface {port} inet manual", ""]

    # Never point a bridge at itself.
    usable_port = port if port and port != bridge else ""
    if ip:
        lines += [
            f"auto {bridge}",
            f"iface {bridge} inet static",
            f"\taddress {ip}/{prefix_length(netmask)}",
        ]
        if gateway:
            lines.append(f"\tgateway {gateway}")
        if usable_port:
            lines.append(f"\tbridge-ports {usable_port}")
        lines += ["\tbridge-stp off", "\tbridge-fd 0"]
        if vlan:
            lines.append("\tbridge-vlan-aware yes")
    else:
        lines += [f"auto {bridge}", f"iface {bridge} inet manual"]
        if usable_port:
            lines.append(f"\tbridge-ports {usable_port}")
    return "\n".join(lines) + "\n"


def render_resolv_conf(cfg: dict) -> str:
    servers = re.split(r"[\s,]+", str(cfg.get("dns") or "").strip())
    return "".join(f"nameserver {s}\n" for s in servers if s)


def render_hosts(hostname: str, ip: str) -> str:
    lines = [
        "127.0.0.1 localhost",
        f"127.0.1.1 {hostname}",
    ]
    if ip and hostname:
        lines.append(f"{ip} {hostname}")
    return "\n".join(lines) + "\n"


def render_storage_entry(storage: dict) -> str:
    storage_id = storage.get("id") or storage.get("storage_id") or "nfs"
    server = storage.get("server") or ""
    export = storage.get("export") or ""
    content = storage.get("content") or "images,iso,backup"
    return (
        f"\nnfs: {storage_id}\n"
        f"\tserver {server}\n"
        f"\texport {export}\n"
        f"\tcontent {content}\n"
        f"\toptions vers=4.2\n"
    )


def merge_storage_cfg(existing: str, storages: list[dict]) -> str:
    content = existing.rstrip("\n")
    for storage in storages:
        sid = storage.get("id") or storage.get("storage_id")
        if not sid:
            continue
        if re.search(rf"^nfs:\s*{re.escape(str(sid))}\s*$", existing, re.MULTILINE):
            continue  # already present
        content += "\n" + render_storage_entry(storage)
    return content.rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Applying configuration
# --------------------------------------------------------------------------
class Applier:
    def __init__(self, root: str = "/", dry_run: bool = False) -> None:
        self.root = root.rstrip("/") or ""
        self.real_root = self.root == ""
        self.dry_run = dry_run
        self.errors: list[str] = []
        self.old_hostname = ""
        self.new_hostname = ""
        # Set when this apply actually changes the running Corosync config, so
        # apply_pve_services knows to restart the cluster stack even on a node
        # that is otherwise already provisioned.
        self.corosync_changed = False

    def path(self, absolute: str) -> Path:
        return Path(f"{self.root}{absolute}")

    def read(self, absolute: str) -> str:
        try:
            return self.path(absolute).read_text()
        except OSError:
            return ""

    def write(self, absolute: str, content: str, mode: int | None = None) -> None:
        self.write_bytes(absolute, content.encode("utf-8"), mode=mode)

    def write_soft(self, absolute: str, content: str, mode: int | None = None) -> None:
        """Like ``write`` but a failure is only logged, never raised.

        Used for pmxcfs-backed files that may legitimately be read-only at this
        point (e.g. /etc/pve/corosync.conf before the node has quorum).
        """
        try:
            self.write(absolute, content, mode=mode)
        except OSError as exc:
            log(f"note: could not write {absolute}: {exc}")

    def write_bytes(self, absolute: str, data: bytes, mode: int | None = None) -> None:
        target = self.path(absolute)
        if self.dry_run:
            log(f"[dry-run] would write {target}")
            return
        self.ensure_parent(target)
        if target.is_file():
            try:
                if target.read_bytes() == data:
                    if mode is not None:
                        os.chmod(target, mode)
                    return
            except OSError:
                pass
            # Best-effort backup of the previous content. Never make the write
            # fail because of it, and never drop a stray backup into pmxcfs
            # (it needs quorum and would be replicated cluster-wide).
            if not absolute.startswith("/etc/pve"):
                try:
                    shutil.copy2(target, f"{target}.pve-agent.bak")
                except OSError:
                    pass
            target.write_bytes(data)
        else:
            self.create_file(target, data)
        if mode is not None:
            try:
                os.chmod(target, mode)
            except OSError:
                pass
        log(f"wrote {target}")

    def ensure_parent(self, target: Path) -> None:
        """Create the parent directory, tolerating pmxcfs quirks.

        On the `/etc/pve` cluster filesystem `mkdir` reports ``EEXIST`` for a
        directory that already exists (e.g. `/etc/pve/qemu-server`), which
        ``mkdir(exist_ok=True)`` does not always swallow. Only create it when
        it really is missing, and ignore a spurious ``EEXIST`` either way.
        """
        parent = target.parent
        if parent.is_dir():
            return
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            pass
        except OSError:
            if not parent.is_dir():
                raise

    def create_file(self, target: Path, data: bytes) -> None:
        """Create a not-yet-existing file and write its content.

        pmxcfs (`/etc/pve`) is picky about *how* a file comes into being and
        rejects some creation paths outright (``ENOENT`` / ``EEXIST``). Try the
        straightforward paths first, then lower-level syscalls and finally the
        coreutils Proxmox ships; the first one that actually leaves the file in
        place wins.
        """
        attempts = (
            ("touch", self._create_touch),
            ("open", self._create_open),
            ("install", self._create_install),
            ("cat", self._create_cat),
        )
        last: OSError | None = None
        for label, create in attempts:
            try:
                create(target, data)
            except OSError as exc:
                last = exc
                log(f"note: {label} could not create {target}: {exc}")
                continue
            if target.is_file():
                return
        if target.is_file():
            return
        raise last or OSError(f"could not create {target}")

    @staticmethod
    def _create_touch(target: Path, data: bytes) -> None:
        # pmxcfs: create the (empty) file first, then write into it.
        target.touch()
        with open(target, "wb") as handle:
            handle.write(data)

    @staticmethod
    def _create_open(target: Path, data: bytes) -> None:
        # Lower-level: create + write through a single file descriptor.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    @staticmethod
    def _create_install(target: Path, data: bytes) -> None:
        # Stage the content in /tmp and let install(1) create the target.
        with tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            result = run(["install", "-m", "0644", tmp_path, str(target)])
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or f"install failed ({result.returncode})")

    @staticmethod
    def _create_cat(target: Path, data: bytes) -> None:
        # Last resort: feed the content to the shell's cat via stdin.
        proc = subprocess.run(
            ["/bin/sh", "-c", 'cat > "$1"', "sh", str(target)],
            input=data,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            reason = proc.stderr.decode("utf-8", "replace").strip()
            raise OSError(reason or f"cat failed ({proc.returncode})")

    def step(self, name: str, func) -> None:
        try:
            func()
        except Exception as exc:  # keep going, report at the end
            self.errors.append(f"{name}: {exc}")
            log(f"ERROR in {name}: {exc}")

    def apply(self, cfg: dict, nic: str | None, restore_vms: bool = True) -> None:
        self.new_hostname = (cfg.get("name") or "").strip()
        self.old_hostname = self.current_hostname()
        # 1. Identity first: the hostname and the node name everywhere it shows
        #    up (hosts/hostname, the Corosync config). This must happen before
        #    pve-cluster is (re)started so pmxcfs adopts the new node name.
        self.step("hostname", lambda: self.apply_hostname(cfg))
        self.step("mail", lambda: self.apply_mail())
        self.step("corosync-name", lambda: self.apply_corosync_name())
        self.step("network", lambda: self.apply_network(cfg, nic))
        self.step("cluster-hosts", lambda: self.apply_cluster_hosts(cfg))
        # 2. Local Corosync config (not in pmxcfs) so the node can reach quorum.
        self.step("corosync-local", lambda: self.apply_corosync_local(cfg))
        self.step("cluster-up", lambda: self.ensure_cluster_quorum(cfg))
        # 3. Everything living in pmxcfs is read-only until quorum: wait for it
        #    to go read-write before touching storage/corosync/SSH/guests.
        self.step("pve-writable", lambda: self.wait_for_pve_writable())
        self.step("corosync", lambda: self.apply_corosync(cfg))
        self.step("cluster-ca", lambda: self.apply_cluster_ca(cfg))
        self.step("ssh", lambda: self.apply_ssh(cfg))
        self.step("storage", lambda: self.apply_storage(cfg))
        self.step("rrdcached", lambda: self.apply_rrdcached())
        self.step("reload", lambda: self.reload_network())
        # 4. Restart the services on the final config, then restore guests.
        self.step("pve-services", lambda: self.apply_pve_services(cfg))
        self.step("vms", lambda: self.apply_vms(cfg, restore=restore_vms))

    def current_hostname(self) -> str:
        return self.read("/etc/hostname").strip() or os.uname().nodename

    # -- hostname (Proxmox: /etc/hosts + /etc/hostname) ---------------------
    def apply_hostname(self, cfg: dict) -> None:
        new = self.new_hostname
        if not new:
            return
        self.write("/etc/hostname", new + "\n")
        self._update_hosts(cfg, self.old_hostname, new)
        # pmxcfs derives the local node name from the *kernel* hostname, so this
        # must happen before pve-cluster is restarted (see apply_pve_services).
        if self.real_root and not self.dry_run:
            run(["hostnamectl", "set-hostname", new])

    def _update_hosts(self, cfg: dict, old: str, new: str) -> None:
        ip = cfg.get("ip_address") or ""
        text = self.read("/etc/hosts")
        if not text:
            self.write("/etc/hosts", render_hosts(new, ip))
            return
        text = replace_hostname(text, old, new)
        mapped = False
        lines: list[str] = []
        for line in text.splitlines():
            tokens = line.split("#", 1)[0].split()
            if len(tokens) >= 2 and _looks_ipv4(tokens[0]) and any(
                name == new or name.split(".", 1)[0] == new for name in tokens[1:]
            ):
                mapped = True
                if ip and tokens[0] != ip and not tokens[0].startswith("127."):
                    line = line.replace(tokens[0], ip, 1)
            lines.append(line)
        if not mapped and ip:
            lines.append(f"{ip} {new}")
        self.write("/etc/hosts", "\n".join(lines) + "\n")

    # -- mail name + postfix ------------------------------------------------
    def apply_mail(self) -> None:
        new = self.new_hostname
        if not new:
            return
        self.write("/etc/mailname", new + "\n")
        postfix = self.path("/etc/postfix/main.cf")
        if not postfix.is_file():
            return
        try:
            text = postfix.read_text()
        except OSError:
            return
        text = replace_hostname(text, self.old_hostname, new)
        text = re.sub(r"(?m)^myhostname\s*=.*$", f"myhostname = {new}", text)
        self.write("/etc/postfix/main.cf", text)

    # -- rename the node inside an existing corosync.conf -------------------
    def apply_corosync_name(self) -> None:
        text = self.read("/etc/pve/corosync.conf")
        if not text:
            return
        updated = replace_hostname(text, self.old_hostname, self.new_hostname)
        if updated != text:
            self.write_soft("/etc/pve/corosync.conf", updated)
            self._mirror_corosync_conf_local(updated)

    def _mirror_corosync_conf_local(self, content: str) -> bool:
        """Write the corosync config to the file Corosync actually reads.

        ``/etc/pve/corosync.conf`` lives in pmxcfs, but Corosync reads
        ``/etc/corosync/corosync.conf``. pmxcfs only copies its own copy over
        when the config version is not *older* than the local file
        (``dcdb_sync_corosync_conf`` logs "local corosync.conf is newer"
        otherwise). A netbooted image ships a stale local file, so the desired
        config never reaches Corosync, the node never reaches quorum and no
        certificate or guest config can be written into pmxcfs (which is
        read-only without quorum). Writing the file directly makes Corosync use
        the intended config regardless of pmxcfs' version comparison.

        Returns True if the local file was actually changed.
        """
        if not content:
            return False
        try:
            if self.path("/etc/corosync/corosync.conf").read_text() == content:
                return False
        except OSError:
            pass
        self.write("/etc/corosync/corosync.conf", content, mode=0o644)
        return True

    # -- network ------------------------------------------------------------
    def apply_network(self, cfg: dict, nic: str | None) -> None:
        if not nic:
            raise RuntimeError("no interface to configure")
        content = render_interfaces(nic, cfg)
        # Operator-provided extra stanzas (/etc/network/interfaces syntax).
        extra = _config_text(cfg.get("network"))
        if extra:
            content = content.rstrip("\n") + "\n\n" + extra + "\n"
        self.write("/etc/network/interfaces", content)
        resolv = render_resolv_conf(cfg)
        if resolv:
            self.write("/etc/resolv.conf", resolv)

    def apply_corosync_local(self, cfg: dict) -> None:
        """Write the file Corosync actually reads, plus the shared authkey.

        ``/etc/corosync/corosync.conf`` is the local config: it is *not* in
        pmxcfs, must be in place before Corosync can start, and is the only copy
        that matters while the cluster filesystem is still read-only.
        """
        if not cfg.get("cluster"):
            return
        conf = cfg.get("corosync_conf")
        if conf:
            content = conf if conf.endswith("\n") else conf + "\n"
        else:
            content = self.read("/etc/corosync/corosync.conf") or self.read(
                "/etc/pve/corosync.conf"
            )
        # Keep the local file (the one Corosync reads) in sync; see
        # _mirror_corosync_conf_local for why pmxcfs alone is not enough.
        if self._mirror_corosync_conf_local(content):
            self.corosync_changed = True
        # The authkey must be byte-identical on every node or Corosync refuses to
        # start ("Could not open /etc/corosync/authkey").
        authkey = cfg.get("corosync_authkey") or ""
        if authkey:
            try:
                raw = base64.b64decode(authkey, validate=True)
            except (ValueError, TypeError):
                raw = b""
            if raw:
                self.write_bytes("/etc/corosync/authkey", raw, mode=0o600)

    def apply_corosync(self, cfg: dict) -> None:
        """Persist the cluster-wide Corosync master into pmxcfs.

        ``/etc/pve/corosync.conf`` is the replicated master: pmxcfs hands it to
        every node and tracks ``config_version`` there. It can only be written
        once the node has quorum, so this runs *after* ``wait_for_pve_writable``.
        The local file Corosync reads is written separately by
        ``apply_corosync_local``.
        """
        if not cfg.get("cluster"):
            return
        conf = cfg.get("corosync_conf")
        if not conf:
            return
        content = conf if conf.endswith("\n") else conf + "\n"
        self.write("/etc/pve/corosync.conf", content)

    def apply_cluster_ca(self, cfg: dict) -> None:
        """Install the cluster root CA so `pvecm updatecerts` can sign the node.

        Without `/etc/pve/priv/pve-root-ca.key` the certificate repair fails with
        "Could not find private key from /etc/pve/priv/pve-root-ca.key". The CA
        is cluster-wide and identical everywhere, so it is shipped in the config.
        """
        self.ensure_root_ca(cfg)

    def ensure_root_ca(self, cfg: dict) -> bool:
        """Write the cluster root CA (key + cert) if the config carries one."""
        ca = cfg.get("cluster_ca") or {}
        key = (ca.get("key") or "").strip("\n")
        pem = (ca.get("pem") or "").strip("\n")
        if not key or not pem:
            return False
        self.write("/etc/pve/priv/pve-root-ca.key", key + "\n", mode=0o600)
        self.write("/etc/pve/pve-root-ca.pem", pem + "\n", mode=0o644)
        return True

    def apply_ssh(self, cfg: dict) -> None:
        """Install the node's SSH key and the cluster-wide trust files."""
        ssh = cfg.get("ssh") or {}
        private = (ssh.get("private") or "").strip("\n")
        public = (ssh.get("public") or "").strip()
        if not private:
            return

        self.write("/root/.ssh/id_rsa", private + "\n", mode=0o600)
        if public:
            self.write("/root/.ssh/id_rsa.pub", public + "\n", mode=0o644)

        authorized = (ssh.get("authorized_keys") or "").strip()
        if authorized:
            content = authorized if authorized.endswith("\n") else authorized + "\n"
            self.write("/root/.ssh/authorized_keys", content, mode=0o644)
            self.write("/etc/pve/priv/authorized_keys", content, mode=0o600)

        known = (ssh.get("known_hosts") or "").strip()
        if known:
            content = known if known.endswith("\n") else known + "\n"
            self.write("/etc/pve/priv/known_hosts", content, mode=0o600)

        if self.real_root and not self.dry_run:
            try:
                os.makedirs(self.path("/root/.ssh"), mode=0o700, exist_ok=True)
                os.chmod(self.path("/root/.ssh"), 0o700)
            except OSError:
                pass

    def apply_cluster_hosts(self, cfg: dict) -> None:
        """Replicate every cluster node's name/IP into /etc/hosts."""
        entries = cfg.get("cluster_hosts") or []
        if not entries:
            return
        existing_names: set[str] = set()
        lines = self.read("/etc/hosts").splitlines()
        for line in lines:
            for token in line.split("#", 1)[0].split()[1:]:
                existing_names.add(token)
        added = False
        for entry in entries:
            ip = str(entry.get("ip") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not ip or not name or name in existing_names:
                continue
            lines.append(f"{ip} {name}")
            existing_names.add(name)
            added = True
        if added:
            self.write("/etc/hosts", "\n".join(lines).rstrip("\n") + "\n")

    def apply_storage(self, cfg: dict) -> None:
        storages = cfg.get("storages") or []
        # Operator-provided extra entries (/etc/pve/storage.cfg syntax).
        raw = _config_text(cfg.get("storage"))
        if not storages and not raw:
            return
        existing = self.read("/etc/pve/storage.cfg")
        content = merge_storage_cfg(existing, storages) if storages else existing
        if raw:
            content = (content.rstrip("\n") + "\n\n" + raw).strip("\n") + "\n"
        self.write("/etc/pve/storage.cfg", content)

    def apply_vms(self, cfg: dict, restore: bool = True) -> None:
        """Restore VM configs that are missing on this node.

        ``restore`` is only true during startup: a freshly reset node lost its
        guest configs, so the panel's stored set is written back. Afterwards the
        node is authoritative - nothing is restored, so a VM the operator
        deleted is never resurrected; the panel instead prunes its record on the
        node's next VM report. Existing files are always left untouched, so a
        running VM is never clobbered.
        """
        if not restore:
            return
        restored = 0
        for vm in cfg.get("vm_configs") or []:
            try:
                vmid = int(vm.get("vmid"))
            except (TypeError, ValueError):
                continue
            config = str(vm.get("config") or "")
            if vmid < 1 or not config:
                continue
            target = self.path(f"/etc/pve/qemu-server/{vmid}.conf")
            if target.is_file():
                continue
            try:
                self.write(
                    f"/etc/pve/qemu-server/{vmid}.conf",
                    config if config.endswith("\n") else config + "\n",
                )
            except OSError as exc:
                # One bad VM must never stop the rest from being restored, but a
                # failed restore must also block startup from completing - a node
                # that could not write a guest must not be allowed to prune it.
                log(f"could not restore VM {vmid}: {exc}")
                self.errors.append(f"vm {vmid}: {exc}")
                continue
            restored += 1
        if restored:
            log(f"restored {restored} VM config(s)")

    # -- rrdcached history dirs (Proxmox renames these on a name change) -----
    def apply_rrdcached(self) -> None:
        old, new = self.old_hostname, self.new_hostname
        if not old or not new or old == new:
            return
        for kind in ("pve2-node", "pve2-storage"):
            source = self.path(f"/var/lib/rrdcached/db/{kind}/{old}")
            target = self.path(f"/var/lib/rrdcached/db/{kind}/{new}")
            if not source.is_dir() or target.exists():
                continue
            if self.dry_run:
                log(f"[dry-run] would rename {source} -> {target}")
            else:
                shutil.move(str(source), str(target))
                log(f"renamed {source} -> {target}")

    def reload_network(self) -> None:
        if self.dry_run or not (self.real_root and os.path.exists("/etc/pve")):
            return  # only reload on a booted Proxmox system
        for cmd in (["ifreload", "-a"], ["systemctl", "restart", "corosync"]):
            result = run(cmd)
            if result.returncode != 0:
                log(f"note: {' '.join(cmd)} -> {result.stderr.strip()}")

    def ensure_cluster_quorum(self, cfg: dict) -> None:
        """Start Corosync on the intended config and wait for quorum.

        Everything below ``nodes/`` in pmxcfs (node directories, certificates,
        SSH trust, storage and guest configs) is read-only without quorum, so a
        freshly netbooted node cannot be provisioned until Corosync is up on the
        right configuration. This runs after ``apply_corosync`` wrote the local
        ``/etc/corosync/corosync.conf`` but before the pmxcfs-backed writes, so
        those no longer fail with ``Permission denied``.
        """
        if self.dry_run or not (self.real_root and os.path.exists("/etc/pve")):
            return
        # Corosync and pmxcfs should agree on the config; make sure the local
        # file (re)written by pmxcfs at startup did not revert the intended one.
        desired = cfg.get("corosync_conf")
        if desired:
            desired = desired if desired.endswith("\n") else desired + "\n"
        changed = self._mirror_corosync_conf_local(desired) if desired else False
        if changed or not self._cfs_quorate():
            self._run("systemctl", "restart", "corosync")
            # Give the node quorum right away so pmxcfs turns read-write even
            # before every member is online. Corosync recalculates this once the
            # real votes are visible.
            self._run("pvecm", "expected", "1")
            if not self._wait_for_quorum(90):
                log("note: node not quorate yet; pmxcfs writes may be rejected")
        # The pmxcfs copy (/etc/pve/corosync.conf) is written later, once
        # wait_for_pve_writable confirms the cluster filesystem is read-write.

    # -- PVE certificate / service repair -----------------------------------
    def _run(self, *cmd: str) -> subprocess.CompletedProcess | None:
        """Run a command, logging its outcome; never raises."""
        argv = [str(part) for part in cmd]
        if self.dry_run:
            log(f"[dry-run] would run: {' '.join(argv)}")
            return None
        try:
            result = run(argv)
        except OSError as exc:
            log(f"note: {' '.join(argv)} -> {exc}")
            return None
        if result.returncode != 0:
            log(f"note: {' '.join(argv)} -> {(result.stderr or result.stdout).strip()}")
        else:
            log(f"ran: {' '.join(argv)}")
        return result

    def _kernel_hostname(self) -> str:
        try:
            return socket.gethostname().split(".", 1)[0]
        except OSError:
            return ""

    def _wait_for_pve_mounted(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists("/etc/pve"):
                return True
            time.sleep(1)
        return os.path.exists("/etc/pve")

    def _pve_writable(self) -> bool:
        """True when `/etc/pve` accepts writes (pmxcfs is rw / has quorum)."""
        probe = self.path("/etc/pve/.pve-agent-probe")
        try:
            with open(probe, "w") as handle:
                handle.write("")
        except OSError:
            return False
        try:
            os.unlink(probe)
        except OSError:
            pass
        return True

    def wait_for_pve_writable(self, timeout: float = 300) -> bool:
        """Poll every second until pmxcfs is no longer read-only.

        Everything backed by pmxcfs (storage, the Corosync master, SSH trust,
        guest configs) is read-only until the node has quorum. Rather than write
        too early and fail, wait for the filesystem to go read-write.
        """
        if self.dry_run or not self.real_root:
            return True
        if not self._wait_for_pve_mounted(30):
            log("note: /etc/pve is not mounted; cannot wait for writability")
            return False
        deadline = time.monotonic() + timeout
        while True:
            if self._pve_writable():
                log("/etc/pve is writable")
                return True
            if time.monotonic() >= deadline:
                log("note: /etc/pve stayed read-only; pmxcfs writes may fail")
                return False
            time.sleep(1)

    def _local_node(self) -> str:
        """Node name ``/etc/pve/local`` currently points at (via pmxcfs)."""
        try:
            return os.readlink("/etc/pve/local").rsplit("/", 1)[-1]
        except OSError:
            return ""

    def _cfs_quorate(self) -> bool:
        # pmxcfs signals quorum by toggling the owner-write bit on /etc/pve/local
        # (this is exactly what PVE::Cluster::check_cfs_quorum() tests).
        try:
            return bool(os.lstat("/etc/pve/local").st_mode & 0o200)
        except OSError:
            return False

    def _wait_for_quorum(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if self._cfs_quorate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(2)

    def _wait_for_local_node(self, name: str, timeout: float) -> bool:
        if not name:
            return True
        deadline = time.monotonic() + timeout
        while True:
            if self._local_node() == name:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)

    def _ssl_pair_ok(self) -> bool:
        """True if /etc/pve/local has a loadable, matching key/cert pair."""
        key = Path("/etc/pve/local/pve-ssl.key")
        cert = Path("/etc/pve/local/pve-ssl.pem")
        try:
            if key.stat().st_size == 0 or cert.stat().st_size == 0:
                return False
        except OSError:
            return False
        key_pub = run(["openssl", "pkey", "-in", str(key), "-pubout"])
        cert_pub = run(["openssl", "x509", "-in", str(cert), "-noout", "-pubkey"])
        if key_pub.returncode != 0 or cert_pub.returncode != 0:
            return False
        return key_pub.stdout.strip() == cert_pub.stdout.strip()

    @staticmethod
    def _missing_root_ca(result) -> bool:
        """True when ``pvecm updatecerts`` failed because the CA key is absent."""
        if result is None:
            return False
        output = (result.stderr or "") + (result.stdout or "")
        return "pve-root-ca.key" in output

    def _generate_node_cert_direct(self, ip: str) -> None:
        """Fall back to PVE's own generator when ``pvecm updatecerts`` fails.

        ``gen_pve_node_files`` performs the same work as updatecerts but without
        its quorum gate, so a node whose quorum is flapping can still get its
        certificate.
        """
        node = self.new_hostname or self._local_node()
        if not node:
            return
        program = (
            "my ($n, $ip) = @ARGV; "
            "$ip = eval { PVE::Cluster::remote_node_ip($n) } "
            "if !defined($ip) || $ip eq ''; "
            "PVE::Cluster::Setup::gen_pve_node_files($n, $ip, 1);"
        )
        args = [
            "perl", "-MPVE::Cluster::Setup", "-MPVE::INotify", "-MPVE::Cluster",
            "-e", program, node,
        ]
        if ip:
            args.append(ip)
        self._run(*args)

    def apply_pve_services(self, cfg: dict) -> None:
        """Guarantee usable node TLS material and running PVE daemons.

        A netbooted image ships the node files of whatever hostname it was built
        under. Once the hostname changes, ``/etc/pve/local`` points at a node
        directory without a ``pve-ssl.key`` and pveproxy crash-loops with
        "failed to load local private key". Two things have to line up:

        * pmxcfs only adopts the new node name (and rewrites the ``local``
          symlink) when pve-cluster (re)starts,
        * the file Corosync reads is ``/etc/corosync/corosync.conf``, which
          pmxcfs only refreshes when its own ``config_version`` is not older
          (see ``_mirror_corosync_conf_local``), and
        * ``pvecm updatecerts`` does nothing at all without quorum, which is
          typically not yet reached right after the network/corosync restart.

        So we restart the cluster stack, wait for the node name and quorum, and
        only then regenerate the certificates - verifying the result before the
        API daemons are (re)started.
        """
        if self.dry_run or not self.real_root:
            return
        if not self._wait_for_pve_mounted(30):
            log("note: /etc/pve is not mounted; skipping certificate/service repair")
            return

        if self.new_hostname and self._kernel_hostname() != self.new_hostname:
            self._run("hostnamectl", "set-hostname", self.new_hostname)

        # Fast path: already consistent, just make sure the daemons are running.
        # Skipped when Corosync's config changed (e.g. a node joined the
        # cluster) so the cluster stack is restarted to pick it up.
        target = self.new_hostname or self._local_node()
        if self._local_node() == target and self._ssl_pair_ok() and not self.corosync_changed:
            self._run("systemctl", "start", "pvedaemon", "pveproxy", "pvestatd")
            return

        # pmxcfs picks up the new node name only on pve-cluster start.
        self._run("systemctl", "restart", "pve-cluster")
        self._wait_for_local_node(self.new_hostname, 60)

        # pve-cluster may have (re)written /etc/corosync/corosync.conf from its
        # database, but it skips that when the local file is "newer" (see
        # _mirror_corosync_conf_local). Write the intended config again *after*
        # the restart and right before Corosync reads it - otherwise the node
        # keeps running the stale cluster config, never reaches quorum and
        # cannot write its certificate into pmxcfs.
        desired = cfg.get("corosync_conf") or self.read("/etc/pve/corosync.conf")
        if desired:
            desired = desired if desired.endswith("\n") else desired + "\n"
            self._mirror_corosync_conf_local(desired)

        # Corosync only reads the local file we just wrote on (re)start.
        self._run("systemctl", "restart", "corosync")
        self._run("pvecm", "expected", "1")
        if not self._wait_for_quorum(120):
            log("note: node did not reach quorum; certificates may stay stale")

        # With quorum available, persist the corosync config into pmxcfs too: the
        # write in apply_corosync may have been rejected while the node was
        # still read-only (no quorum).
        if cfg.get("corosync_conf") and desired:
            self.write("/etc/pve/corosync.conf", desired)

        # The node certificate is signed by the cluster root CA; make sure it is
        # in place before asking pvecm to (re)generate the certificate.
        self.ensure_root_ca(cfg)

        # updatecerts can still see a stale (no-quorum) state right after the
        # corosync restart, so retry until the certificate is actually usable.
        for _ in range(6):
            result = self._run("pvecm", "updatecerts", "--force")
            if self._ssl_pair_ok():
                break
            # Classic failure: the cluster root CA is missing. Re-install it from
            # the config and retry right away instead of failing the whole run.
            if self._missing_root_ca(result):
                log("note: cluster root CA was missing; re-installing it")
                if self.ensure_root_ca(cfg):
                    time.sleep(2)
                    continue
            time.sleep(5)

        if not self._ssl_pair_ok():
            log("note: node certificate still unusable; generating it directly")
            self._generate_node_cert_direct(cfg.get("ip_address") or "")

        if not self._ssl_pair_ok():
            log("ERROR: could not create a usable node certificate for /etc/pve/local")

        self._run("systemctl", "restart", "pvedaemon", "pveproxy", "pvestatd")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def truthy(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


CONF_FILENAMES = ("pve-agent.conf", "agent.conf", "pve.conf")


def _parse_conf(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            values[key] = value
    return values


def load_conf_file(explicit: str | None = None) -> Path | None:
    """Load a KEY=value config file into the environment (existing values win).

    Searched automatically (first hit wins):
      1. --conf PATH            (or PVE_CONF environment variable)
      2. /etc/pve-agent.conf
      3. pve-agent.conf / agent.conf / pve.conf next to this script
      4. the same names in the current working directory
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("PVE_CONF"):
        candidates.append(Path(os.environ["PVE_CONF"]))
    candidates.append(Path("/etc/pve-agent.conf"))
    script_dir = Path(__file__).resolve().parent
    for name in CONF_FILENAMES:
        candidates.append(script_dir / name)
        candidates.append(Path.cwd() / name)

    for path in candidates:
        if path.is_file():
            for key, value in _parse_conf(path).items():
                os.environ.setdefault(key, value)
            return path
    return None


def resolve_settings(args) -> dict:
    cmdline = read_cmdline()
    controller = (args.ctrl or env("PVE_CTRL") or cmdline.get("ctrl") or "").rstrip("/")
    return {
        "controller": controller,
        "token": args.token or env("PVE_API_TOKEN") or cmdline.get("api_token"),
        "iface": args.iface or env("PVE_IFACE") or cmdline.get("iface"),
        "mac": args.mac or env("PVE_MAC") or cmdline.get("mac"),
        "root": args.root or env("PVE_ROOT") or "/",
        "report_only": args.report_only or truthy(env("PVE_REPORT_ONLY")),
        "dry_run": args.dry_run or truthy(env("PVE_DRY_RUN")),
        # The agent uses a persistent WebSocket by default; HTTP polling is a
        # fallback (--http / PVE_HTTP=true, or when no ws URL can be derived).
        "ws_url": (
            args.ws or env("PVE_WS_URL") or cmdline.get("ws") or derive_ws_url(controller)
        ).rstrip("/"),
        "http_only": bool(getattr(args, "http", False) or truthy(env("PVE_HTTP"))),
        # Permanent by default: the agent keeps polling so a node that boots
        # before it is accepted in the panel still gets configured. Opt out with
        # --once or PVE_ONCE=true.
        "watch": not (args.once or truthy(env("PVE_ONCE"))),
        "interval": args.interval or _int(env("PVE_INTERVAL"), 5),
        "timeout": _int(args.timeout if args.timeout is not None else env("PVE_TIMEOUT"), 20),
    }


def attempt(s: dict, iface: str | None, hardware: dict, last_signature: str | None) -> tuple[str, str | None]:
    """Report once. Returns (status, config signature).

    status: pending | unchanged | report_only | applied | error | apply_error
    """
    try:
        response = report_hardware(s["controller"], s["token"], hardware, s["timeout"])
    except (RuntimeError, urllib.error.URLError, OSError) as exc:
        log(f"report failed: {exc}")
        return "error", None

    if response.get("rejected"):
        log("node was rejected by the controller; stopping")
        return "rejected", None

    if not response.get("registered"):
        log(response.get("message") or "node is not registered yet")
        return "pending", None

    config = response.get("config") or {}
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
    if signature == last_signature:
        return "unchanged", signature

    if s["report_only"]:
        log("report-only: configuration not applied")
        return "report_only", signature

    applier = Applier(root=s["root"], dry_run=s["dry_run"])
    applier.apply(config, iface, restore_vms=not s.get("startup_done", False))
    if applier.errors:
        log("finished with errors:")
        for error in applier.errors:
            log(f"  - {error}")
        return "apply_error", signature
    s["startup_done"] = True
    log("configuration applied")
    return "applied", signature


def once(s: dict, iface: str | None, hardware: dict) -> int:
    status, _ = attempt(s, iface, hardware, None)
    return 0 if status in ("pending", "rejected", "unchanged", "report_only", "applied") else 1


def apply_config(s: dict, iface: str | None, config: dict, last_signature: str | None) -> tuple[str, str | None]:
    """Apply a pushed config if it changed. Returns (status, signature)."""
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
    if signature == last_signature:
        return "unchanged", signature
    if s["report_only"]:
        log("report-only: configuration not applied")
        return "report_only", signature
    applier = Applier(root=s["root"], dry_run=s["dry_run"])
    applier.apply(config, iface, restore_vms=not s.get("startup_done", False))
    if applier.errors:
        log("finished with errors:")
        for error in applier.errors:
            log(f"  - {error}")
        return "apply_error", signature
    s["startup_done"] = True
    log("configuration applied")
    return "applied", signature


def resolve_identity(s: dict) -> tuple[str | None, str]:
    """Return ``(interface, mac)`` for the NIC that reaches the controller."""
    iface = s["iface"] or detect_interface(s["controller"])
    mac = (s["mac"] or (interface_mac(iface) if iface else "")).lower()
    return iface, mac


def _wait_for_identity(s: dict) -> tuple[str | None, str]:
    """Block until the network/interface is ready (used by HTTP polling mode)."""
    warned = False
    while True:
        iface, mac = resolve_identity(s)
        if mac:
            return iface, mac
        if not warned:
            log("network/interface not ready yet; retrying every 5s")
            log(f"  interfaces: {list_interfaces()}")
            warned = True
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            return iface, ""


def run_websocket(s: dict) -> int:
    """Keep one WebSocket open; request and apply the full config at startup.

    On every (re)connection the agent resolves its NIC/MAC (retrying until the
    network is up), sends the hardware inventory and explicitly asks for the full
    configuration. The controller's reply installs everything the node can get
    (hostname, management network, extra interfaces, cluster, Corosync + authkey,
    storages, SSH keys, /etc/hosts), so a freshly booted node configures itself.
    Never exits except on Ctrl-C.
    """
    url = s["ws_url"]
    interval = max(1, _int(s.get("interval"), 5))
    s.setdefault("startup_done", False)
    log(f"connecting to {url} (full config requested on connect)")
    signature: str | None = None
    backoff = 1
    warned_interfaces = False
    while True:
        iface, mac = resolve_identity(s)
        if not mac:
            if not warned_interfaces:
                log("network/interface not yet available; retrying every 5s")
                log(f"  interfaces: {list_interfaces()}")
                warned_interfaces = True
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                return 0
            continue

        hardware = collect_hardware(iface, mac)
        hello = {"type": "hello", "version": VERSION, **hardware}
        log(
            f"interface {iface or '?'}, MAC {mac} "
            f"({hardware['cpu_count']} vCPU, {hardware['ram_gb']} GB RAM)"
        )

        ws = WebSocket(url, s["token"], s["timeout"])
        try:
            ws.connect()
            log("websocket connected")
            backoff = 1
            sampler = ResourceSampler()
            ws.send_text(json.dumps(hello))
            vm_bases = default_vm_bases()
            vms = collect_vms(vm_bases)
            last_vms_sig = vms_signature(vms)
            # No pruning until startup has completed cleanly (see apply_config).
            ws.send_text(json.dumps(vms_message(vms, s.get("startup_done", False))))
            ws.send_text(json.dumps({"type": "sync"}))  # request everything now
            last_metrics = 0.0
            while True:
                # Report CPU/RAM on a fixed cadence (independent of messages).
                if time.monotonic() - last_metrics >= interval:
                    try:
                        ws.send_text(json.dumps({"type": "metrics", **sampler.sample()}))
                    except OSError:
                        break
                    last_metrics = time.monotonic()
                    # Rescan VMs on the same cadence so VMs created at runtime
                    # show up in the panel without a reconnect.
                    vm_bases = default_vm_bases()
                    current = collect_vms(vm_bases)
                    sig = vms_signature(current)
                    if sig != last_vms_sig:
                        try:
                            ws.send_text(
                                json.dumps(vms_message(current, s.get("startup_done", False)))
                            )
                        except OSError:
                            break
                        last_vms_sig = sig

                timeout = max(0.2, interval - (time.monotonic() - last_metrics))
                try:
                    raw = ws.recv_text(timeout=timeout)
                except TimeoutError:
                    continue
                if raw is None:
                    log("controller closed the connection")
                    break
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind == "config":
                    status, sig = apply_config(s, iface, message.get("config") or {}, signature)
                    if sig is not None:
                        signature = sig
                    if status in ("applied", "apply_error"):
                        try:
                            ws.send_text(json.dumps({"type": "status", "state": status}))
                        except OSError:
                            break
                        # The config may have redefined our VMs; resync them.
                        vm_bases = default_vm_bases()
                        refreshed = collect_vms(vm_bases)
                        last_vms_sig = vms_signature(refreshed)
                        try:
                            ws.send_text(
                                json.dumps(vms_message(refreshed, s.get("startup_done", False)))
                            )
                        except OSError:
                            break
                elif kind == "pending":
                    log(message.get("message") or "waiting for the node to be accepted in the panel")
                elif kind == "rejected":
                    log("node was rejected by the controller")
                elif kind == "error":
                    log(f"controller error: {message.get('message')}")
        except KeyboardInterrupt:
            log("interrupted; stopping")
            return 0
        except (OSError, WebSocketClosed, RuntimeError) as exc:
            log(f"websocket error: {exc}")
        finally:
            ws.close()
        log(f"reconnecting in {backoff}s")
        try:
            time.sleep(backoff)
        except KeyboardInterrupt:
            return 0
        backoff = min(backoff * 2, 30)


def watch(s: dict, iface: str | None, hardware: dict) -> int:
    """Poll forever until the node is accepted, then keep it applied.

    Never dies on a transient failure: every iteration is wrapped so a network
    error, a bad response or a failed apply merely logs and retries after
    ``interval`` seconds. Returns only when the operator rejects the node.
    """
    interval = max(1, _int(s["interval"], 5))
    s.setdefault("startup_done", False)
    log(f"watching: polling every {interval}s until the node is accepted")
    signature: str | None = None
    while True:
        try:
            status, sig = attempt(s, iface, hardware, signature)
            if status == "rejected":
                return 0
            if sig is not None:
                signature = sig
        except KeyboardInterrupt:
            log("interrupted; stopping")
            return 0
        except Exception as exc:  # keep the service alive no matter what
            log(f"unexpected error (will retry): {exc}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPXE-PveController provisioning agent")
    parser.add_argument("--ctrl", help="controller base URL")
    parser.add_argument("--token", help="API token (X-API-Token)")
    parser.add_argument("--iface", help="force a network interface")
    parser.add_argument("--mac", help="force a MAC address")
    parser.add_argument("--root", default=None, help="target root (e.g. /mnt/target)")
    parser.add_argument("--report-only", action="store_true", help="only report hardware")
    parser.add_argument("--dry-run", action="store_true", help="do not change anything")
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--watch", action="store_true",
                        help="poll forever (default; kept for compatibility)")
    parser.add_argument("--once", action="store_true",
                        help="report a single time and exit")
    parser.add_argument("--ws", default=None,
                        help="agent WebSocket URL (default: derived from --ctrl, same host/port)")
    parser.add_argument("--http", action="store_true",
                        help="use HTTP polling instead of the WebSocket")
    parser.add_argument("--interval", type=int, default=None, help="poll interval in seconds (default 5)")
    parser.add_argument("--conf", default=None, help="path to a pve-agent.conf file")
    args = parser.parse_args(argv)

    conf_path = None
    if args.conf and not Path(args.conf).is_file():
        log(f"config file not found: {args.conf}")
        return 2
    conf_path = load_conf_file(args.conf)
    if conf_path is not None:
        log(f"loaded config from {conf_path}")

    s = resolve_settings(args)
    controller = s["controller"]
    if not controller:
        log(
            "no controller URL. Set PVE_CTRL in pve-agent.conf "
            "(/etc/pve-agent.conf or next to this script), or pass --ctrl."
        )
        return 2

    if args.once:
        iface, mac = resolve_identity(s)
        if not mac:
            log("could not determine the MAC address")
            return 2
        return once(s, iface, collect_hardware(iface, mac))

    if s["http_only"] or not s["ws_url"]:
        iface, mac = _wait_for_identity(s)
        if not mac:
            return 2
        hardware = collect_hardware(iface, mac)
        log(
            f"interface {iface or '?'}, MAC {mac} "
            f"({hardware['cpu_count']} vCPU, {hardware['ram_gb']} GB RAM) -> {controller}"
        )
        return watch(s, iface, hardware)

    # WebSocket mode resolves its own NIC/MAC on connect (retrying until the
    # network is up), so a slow NIC at boot no longer aborts the agent.
    return run_websocket(s)


if __name__ == "__main__":
    sys.exit(main())
