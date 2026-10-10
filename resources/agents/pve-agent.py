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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "2.0"

_VIRTUAL_RE = re.compile(
    r"^(lo|docker\d*|veth.*|br-[0-9a-f]+|virbr\d*|vnet\d+|tap\d+|tun\d+|wg\d+|cni\d+|flannel.*|cali.*)$"
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
    return subprocess.run(args, capture_output=True, text=True)


def is_virtual(name: str) -> bool:
    return bool(_VIRTUAL_RE.match(name))


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


def _route_interface(target: str | None) -> str | None:
    if target:
        result = run(["ip", "route", "get", target])
        if result.returncode == 0:
            match = re.search(r"\bdev\s+(\S+)", result.stdout)
            if match and not is_virtual(match.group(1)):
                return match.group(1)
    result = run(["ip", "route", "show", "default"])
    if result.returncode == 0:
        match = re.search(r"\bdev\s+(\S+)", result.stdout)
        if match and not is_virtual(match.group(1)):
            return match.group(1)
    return None


def detect_interface(controller: str) -> str | None:
    dev = _route_interface(host_from_url(controller))
    if dev:
        return dev
    net_dir = Path("/sys/class/net")
    if net_dir.is_dir():
        for path in sorted(net_dir.iterdir()):
            name = path.name
            if is_virtual(name):
                continue
            state = path.joinpath("operstate")
            if state.is_file() and state.read_text().strip() == "down":
                continue
            return name
    return None


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


def collect_hardware(iface: str, mac: str) -> dict:
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
    }


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
def render_interfaces(nic: str, cfg: dict) -> str:
    bridge = cfg.get("bridge") or "vmbr0"
    ip = cfg.get("ip_address") or ""
    netmask = cfg.get("netmask") or "255.255.255.0"
    gateway = cfg.get("gateway") or ""
    vlan = cfg.get("vlan")

    port = f"{nic}.{vlan}" if vlan else nic
    lines = ["auto lo", "iface lo inet loopback", ""]
    lines += [f"iface {nic} inet manual", ""]
    if vlan:
        lines += [f"auto {port}", f"iface {port} inet manual", ""]
    if ip:
        lines += [
            f"auto {bridge}",
            f"iface {bridge} inet static",
            f"\taddress {ip}/{prefix_length(netmask)}",
        ]
        if gateway:
            lines.append(f"\tgateway {gateway}")
        lines += [
            f"\tbridge-ports {port}",
            "\tbridge-stp off",
            "\tbridge-fd 0",
        ]
        if vlan:
            lines.append("\tbridge-vlan-aware yes")
    else:
        lines += [f"auto {bridge}", f"iface {bridge} inet manual", f"\tbridge-ports {port}"]
    return "\n".join(lines) + "\n"


def render_resolv_conf(cfg: dict) -> str:
    servers = re.split(r"[\s,]+", str(cfg.get("dns") or "").strip())
    return "".join(f"nameserver {s}\n" for s in servers if s)


def render_hosts(hostname: str, ip: str) -> str:
    lines = [
        "127.0.0.1 localhost",
        "127.0.1.1 {}".format(hostname),
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

    def path(self, absolute: str) -> Path:
        return Path(f"{self.root}{absolute}")

    def read(self, absolute: str) -> str:
        try:
            return self.path(absolute).read_text()
        except OSError:
            return ""

    def write(self, absolute: str, content: str, mode: int | None = None) -> None:
        self.write_bytes(absolute, content.encode("utf-8"), mode=mode)

    def write_bytes(self, absolute: str, data: bytes, mode: int | None = None) -> None:
        target = self.path(absolute)
        if self.dry_run:
            log(f"[dry-run] would write {target}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            try:
                if target.read_bytes() == data:
                    if mode is not None:
                        os.chmod(target, mode)
                    return
            except OSError:
                pass
            shutil.copy2(target, f"{target}.pve-agent.bak")
        target.write_bytes(data)
        if mode is not None:
            try:
                os.chmod(target, mode)
            except OSError:
                pass
        log(f"wrote {target}")

    def step(self, name: str, func) -> None:
        try:
            func()
        except Exception as exc:  # keep going, report at the end
            self.errors.append(f"{name}: {exc}")
            log(f"ERROR in {name}: {exc}")

    def apply(self, cfg: dict, nic: str) -> None:
        self.new_hostname = (cfg.get("name") or "").strip()
        self.old_hostname = self.current_hostname()
        self.step("hostname", lambda: self.apply_hostname(cfg))
        self.step("mail", lambda: self.apply_mail())
        self.step("corosync-name", lambda: self.apply_corosync_name())
        self.step("network", lambda: self.apply_network(cfg, nic))
        self.step("cluster-hosts", lambda: self.apply_cluster_hosts(cfg))
        self.step("corosync", lambda: self.apply_corosync(cfg))
        self.step("ssh", lambda: self.apply_ssh(cfg))
        self.step("storage", lambda: self.apply_storage(cfg))
        self.step("rrdcached", lambda: self.apply_rrdcached())
        self.step("reload", lambda: self.reload_network())
        self.step("pve-services", lambda: self.apply_pve_services())

    def current_hostname(self) -> str:
        return self.read("/etc/hostname").strip() or os.uname().nodename

    # -- hostname (Proxmox: /etc/hosts + /etc/hostname) ---------------------
    def apply_hostname(self, cfg: dict) -> None:
        new = self.new_hostname
        if not new:
            return
        self.write("/etc/hostname", new + "\n")
        self._update_hosts(cfg, self.old_hostname, new)
        if self.real_root and not self.dry_run and os.path.exists("/etc/pve"):
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
            self.write("/etc/pve/corosync.conf", updated)

    # -- network ------------------------------------------------------------
    def apply_network(self, cfg: dict, nic: str) -> None:
        if not nic:
            raise RuntimeError("no interface to configure")
        self.write("/etc/network/interfaces", render_interfaces(nic, cfg))
        resolv = render_resolv_conf(cfg)
        if resolv:
            self.write("/etc/resolv.conf", resolv)

    def apply_corosync(self, cfg: dict) -> None:
        if not cfg.get("cluster"):
            return
        conf = cfg.get("corosync_conf")
        if conf:
            self.write("/etc/pve/corosync.conf", conf if conf.endswith("\n") else conf + "\n")
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
        if not storages:
            return
        existing = self.read("/etc/pve/storage.cfg")
        self.write("/etc/pve/storage.cfg", merge_storage_cfg(existing, storages))

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

    def apply_pve_services(self) -> None:
        """Regenerate PVE certs and restart the cluster/daemon services.

        Runs last, on a booted Proxmox system only. Needs `pvecm updatecerts
        --force` + a restart of pve-cluster/pvedaemon/pveproxy/pvestatd so a
        changed hostname/cluster config is picked up.
        """
        if self.dry_run or not (self.real_root and os.path.exists("/etc/pve")):
            return
        for cmd in (
            ["pvecm", "updatecerts", "--force"],
            ["systemctl", "restart", "pve-cluster", "pvedaemon", "pveproxy", "pvestatd"],
        ):
            try:
                result = run(cmd)
            except OSError as exc:
                log(f"note: {' '.join(cmd)} -> {exc}")
                continue
            if result.returncode != 0:
                output = (result.stderr or result.stdout).strip()
                log(f"note: {' '.join(cmd)} -> {output}")
            else:
                log(f"ran: {' '.join(cmd)}")


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


def attempt(s: dict, iface: str, hardware: dict, last_signature: str | None) -> tuple[str, str | None]:
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
    applier.apply(config, iface)
    if applier.errors:
        log("finished with errors:")
        for error in applier.errors:
            log(f"  - {error}")
        return "apply_error", signature
    log("configuration applied")
    return "applied", signature


def once(s: dict, iface: str, hardware: dict) -> int:
    status, _ = attempt(s, iface, hardware, None)
    return 0 if status in ("pending", "rejected", "unchanged", "report_only", "applied") else 1


def apply_config(s: dict, iface: str, config: dict, last_signature: str | None) -> tuple[str, str | None]:
    """Apply a pushed config if it changed. Returns (status, signature)."""
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
    if signature == last_signature:
        return "unchanged", signature
    if s["report_only"]:
        log("report-only: configuration not applied")
        return "report_only", signature
    applier = Applier(root=s["root"], dry_run=s["dry_run"])
    applier.apply(config, iface)
    if applier.errors:
        log("finished with errors:")
        for error in applier.errors:
            log(f"  - {error}")
        return "apply_error", signature
    log("configuration applied")
    return "applied", signature


def run_websocket(s: dict, iface: str, hardware: dict) -> int:
    """Keep one WebSocket open and apply configuration the controller pushes.

    Reconnects with exponential backoff. Hardware is sent once on (re)connect;
    everything else is initiated by the controller, so the node no longer spams
    the API. Never exits except on Ctrl-C.
    """
    url = s["ws_url"]
    log(f"connecting to {url}")
    hello = {"type": "hello", "version": VERSION, **hardware}
    signature: str | None = None
    backoff = 1
    while True:
        ws = WebSocket(url, s["token"], s["timeout"])
        try:
            ws.connect()
            log("websocket connected; waiting for configuration")
            backoff = 1
            ws.send_text(json.dumps(hello))
            while True:
                try:
                    raw = ws.recv_text(timeout=45)
                except socket.timeout:
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
                        ws.send_text(json.dumps({"type": "status", "state": status}))
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


def watch(s: dict, iface: str, hardware: dict) -> int:
    """Poll forever until the node is accepted, then keep it applied.

    Never dies on a transient failure: every iteration is wrapped so a network
    error, a bad response or a failed apply merely logs and retries after
    ``interval`` seconds. Returns only when the operator rejects the node.
    """
    interval = max(1, _int(s["interval"], 5))
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

    iface = s["iface"] or detect_interface(controller)
    mac = (s["mac"] or (interface_mac(iface) if iface else "")).lower()
    if not mac:
        log("could not determine the MAC address")
        return 2

    hardware = collect_hardware(iface, mac)
    log(
        f"interface {iface or '?'}, MAC {mac} "
        f"({hardware['cpu_count']} vCPU, {hardware['ram_gb']} GB RAM) -> {controller}"
    )

    if args.once:
        return once(s, iface, hardware)
    if s["http_only"] or not s["ws_url"]:
        return watch(s, iface, hardware)
    return run_websocket(s, iface, hardware)


if __name__ == "__main__":
    sys.exit(main())
