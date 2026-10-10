# iPXE-PveController

A web panel + API that **PXE-boots Proxmox VE nodes** and **auto-configures**
them (nodename, IP, networking, VMs, cluster, Corosync, storage) based on the
**MAC address** of each node's provisioning NIC.

Built as a small, Laravel-style MVC framework in Python (Werkzeug + Jinja2),
with Tailwind CSS, Font Awesome and HTMX on the front end. The agent channel is
a WebSocket served on the same port as HTTP (handled in `core/websocket.py`).

---

## Quick start

```bash
# 1. Install system packages (see apt.list for the full list)
sudo apt-get update
grep -vE '^\s*#|^\s*$' apt.list | sudo xargs -r apt-get install -y

# 2. Create a virtualenv and install Python dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Create your .env and generate an application key
python3 main.py key:generate

# 4. Start everything: the server starts and you get a console
python3 main.py
```

That single command **starts the web server and drops you into an interactive
console** at the same time:

```
  iPXE PVE Controller
  interactive console

✔ Server running at http://127.0.0.1:8000
• Type `help` for commands, `exit` to stop and quit.
app > app:user:make
```

Create your first user right there (`app:user:make`), then open
<http://127.0.0.1:8000> in your browser and sign in. Type `exit` (or Ctrl+D) to
stop the server and quit.

> You can still run a single command non-interactively, e.g.
> `python3 main.py app:user:make` or `python3 main.py migrate`, or run the
> server in the foreground without a console via `python3 main.py serve`.

---

## The console

`python3 main.py` (no arguments) runs the server and an interactive console at
the same time. Type `help` to see everything, `exit` (or Ctrl+D) to stop the
server and leave.

| Command | Description |
| --- | --- |
| `app:user:make` | Interactively create a panel user (username, name, password) |
| `app:user:list` | List existing users |
| `app:user:password` | Reset a user's password |
| `migrate` | Run pending database migrations |
| `db:rollback` | Roll back the last migration |
| `db:fresh` | Drop everything and re-run migrations + seed |
| `db:seed` | Seed default settings (PXE next-server, boot file, API token) |
| `key:generate` | Generate a new `APP_KEY` in `.env` |
| `route:list` | List all registered routes |
| `serve` | Run the server in the foreground (normally not needed — it already runs) |
| `about` | Show environment information |

---

## Project structure

```
main.py                  # entry point (console + single-command mode)
config/                  # configuration / .env loader
core/                    # the framework ("mini Laravel")
├── application.py       #   HTTP kernel (middleware pipeline, dispatch)
├── router.py            #   routing + url_for
├── model.py             #   ActiveRecord base + query builder
├── database.py (db/)    #   parameterised SQLite access
├── security.py          #   XSS / SQLi / command-injection guards
├── validation.py        #   rule-based input validation
├── session.py           #   DB-backed sessions
├── auth.py              #   authentication manager
├── middleware.py        #   session, CSRF, auth, security headers
├── view.py              #   Jinja2 view factory (autoescaping on)
└── cli/                 #   console kernel + terminal IO
database/                # SQLite database + migrations
routes/                  # web.py (panel) and api.py (machine/boot API)
resources/
├── controllers/         # controllers (incl. api/ sub-namespace)
├── app/models/          # models (User, Node, Cluster, BootEvent, ...)
├── views/               # Jinja2 templates (layouts/, pages/, partials/)
└── static/              # css / js / icons
scripts/                 # CLI command modules
```

---

## How it works

### Boot flow

1. Your DHCP/router hands out `next-server` (this controller) and
   `boot filename` = `boot.ipxe`.
2. The node fetches the script at `GET /boot.ipxe`, which points it at your
   provisioning image (configure the target under **Settings → pxe.chain_url**).
3. The provisioning agent on the node opens one **persistent WebSocket** to the
   controller (`ws://<host>/agent`, same host/port as the panel) and sends its
   hardware inventory once:

   ```json
   {
     "type": "hello",
     "mac_address": "aa:bb:cc:dd:ee:ff",
     "cpu_model": "AMD EPYC 7402P 24-Core Processor",
     "cpu_count": 24,
     "ram_gb": 128,
     "ip_address": "10.0.0.10"
   }
   ```

4. The controller looks the node up **by MAC**, logs the boot event, refreshes
   the stored hardware data, and pushes the configuration the node should apply
   (name, IP, networking, cluster, Corosync, storage, VMs) over the same socket.
   It **keeps the socket open**, so when you change a node/cluster/storage in the
   panel the fresh config is pushed immediately — no polling. Nodes whose agent
   is connected show a green **live** badge under **Nodes**.

HTTP `POST /api/boot/report` still works as a fallback (and for `pve-report.sh`);
the Python agent only uses it with `--http`.

Unknown MACs are recorded as *unregistered* boot events. They appear under
**Nodes → Pending** (and on the dashboard) with their reported MAC, IP, CPU and
RAM — click **Create** to open a pre-filled node form and provision them, or
**Reject** to dismiss a machine you don't want. Rejections are persisted per MAC
(shown under *Rejected machines* with **Restore** and **Delete** buttons). The
controller answers such a MAC with a `rejected` state, so the agent does not
apply any configuration. **Delete** removes the machine from the system
entirely (the rejection *and* its boot reports), so it neither shows up as
pending nor as rejected until it boots again. Creating a node for a rejected MAC
clears the rejection.

### Storage (NFS)

The **Storage** tab manages NFS shares. Create one with an **ID**, **Server
(IP)** and **Export**:

* While typing the server, the **Export** field loads the server's available
exports (via `showmount -e`) into a dropdown you can pick from or type over.
* Reachability is checked on save, but a storage is created either way; re-check
any time with the refresh button.
* Assign a storage to **individual nodes** (Nodes → edit → Storage) and/or to a
**whole cluster** (Clusters → open a cluster → Storage). A node inherits its
cluster's storage automatically — the effective list is returned by the boot
API in `config.storages`.

### API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check |
| `POST` | `/api/boot/report` | Node reports hardware + gets its config |
| `GET` | `/api/boot/config/<mac>` | Fetch a node's config by MAC |
| `GET` | `/api/nodes` | Read-only list of configured nodes |
| `WS` | `/agent` (same port as HTTP) | Persistent agent socket: hardware in, config pushes out |
| `GET` | `/boot.ipxe` | iPXE boot script |
| `GET` | `/bootfiles/<name>` | Uploaded boot file (vmlinuz, initrd, squashfs, …) |
| `GET` | `/agent/pve-agent.py` | Python provisioning agent (reports + applies config) |
| `GET` | `/agent/pve-agent.service` | systemd unit to run the agent on boot |
| `GET` | `/agent/pve-agent.conf` | EnvironmentFile template for the agent |
| `GET` | `/agent/install.sh` | One-line installer (enables the service, keeps it stopped) |
| `GET` | `/agent/pve-report.sh` | Shell agent (reports hardware only) |

Panel pages (session + CSRF protected): `/dashboard`, `/nodes`,
`/nodes/pending`, `/nodes/create`, `/storage`, `/clusters`, `/clusters/<id>`,
`/boot`, `/settings`.

Machine clients authenticate with the `X-API-Token` header when a token is set
under **Settings → api.token** (generate one via `db:seed`).

### Cluster / Corosync config (auto-generated)

The `corosync.conf` for a cluster is generated **live** from the nodes currently
assigned to it — no manual editing. Open a cluster in the panel
(**Clusters → click a cluster**) to see it, copy it, or download it.

* **Node IDs** — each node uses its ``corosync_node_id`` if set; nodes without
  one (or with a duplicate) automatically get the next free ID, assigned in a
  stable alphabetical order.
* **`config_version`** — bumped automatically whenever the topology changes
  (node added/removed, ID or ring address changed) so Corosync reloads.
* **Ring address** — defaults to the node's management IP; override per node
  with ``{"corosync_ring0_addr": "10.10.0.5"}`` in the node's Network JSON.
* Other `totem`/`logging`/`quorum` values can be overridden via the cluster's
  Corosync JSON (e.g. `token_coefficient`, `link_mode`, `secauth`).

Example output for two nodes:

```
logging {
  debug: off
  to_syslog: yes
}

nodelist {
  node {
    name: pve02
    nodeid: 1
    quorum_votes: 1
    ring0_addr: 192.168.189.211
  }
  node {
    name: ryzen01
    nodeid: 2
    quorum_votes: 1
    ring0_addr: 192.168.189.212
  }
}

quorum {
  provider: corosync_votequorum
}

totem {
  cluster_name: AGC-M1
  config_version: 1
  interface {
    linknumber: 0
  }
  ip_version: ipv4-6
  link_mode: passive
  secauth: on
  token_coefficient: 125
  version: 2
}
```

The generated config is also returned by the boot API in
`config.corosync_conf`, so a node can write it straight to
`/etc/pve/corosync.conf` during provisioning. You can fetch it directly at
`GET /clusters/<id>/corosync.conf` (append `?download=1` for an attachment).

#### Cluster secrets (shared identity material)

Corosync refuses to start without `/etc/corosync/authkey`, and a Proxmox cluster
only forms when every node also trusts the others' SSH keys. The panel is the
**single source of truth** for this material and hands it to each node in the
config payload, so it is identical everywhere and a re-provisioned node slots
back in:

* **Corosync authkey** — 128 random bytes, generated once per cluster, returned
  as `config.corosync_authkey` (base64). The agent writes it to
  `/etc/corosync/authkey` (mode 0600).
* **SSH keys** — each node gets a login keypair (`/root/.ssh/id_rsa[.pub]`) and
  a host keypair, generated by the panel with `ssh-keygen`. The cluster-wide
  `authorized_keys` (all nodes' public keys) and `known_hosts` (all nodes' host
  keys) are assembled and written to `/root/.ssh/authorized_keys` and
  `/etc/pve/priv/{authorized_keys,known_hosts}`.
* **Name resolution** — every cluster node's `IP name` is replicated into
  `/etc/hosts` (`config.cluster_hosts`), so nodes resolve each other by name.

Private keys are marked `hidden` on the models and never appear in panel/JSON
payloads. Open a cluster to see the secret status summary.

### Boot script (iPXE)

The **Boot** tab edits every option of the generated `boot.ipxe` with a live
preview. Upload your kernel/initrd/squashfs under **Boot files**; they are served
at `/bootfiles/<name>` and referenced automatically. The default script is
deliberately **server-independent** (no network interface reference), so the
*same* script works on every machine:

```
#!ipxe
set controller http://10.0.0.5:8000
dhcp
kernel http://10.0.0.5:8000/bootfiles/vmlinuz initrd=initrd.img pve_ctrl=${controller} boot=live fetch=http://10.0.0.5:8000/bootfiles/root.squashfs ip=dhcp
initrd http://10.0.0.5:8000/bootfiles/initrd.img
boot
```

The MAC address is **not** taken from iPXE (that would require knowing the
interface name). The agent inside the image detects the MAC itself. So the
entire setup is: one DHCP config (options 66/67), one `boot.ipxe`, one image —
rack the server, power it on, done.

* **Boot files** — upload `vmlinuz`, `initrd.img`, `root.squashfs`, … (up to
  `BOOTFILE_MAX_MB`, default 8 GB). They are served with HTTP Range support and
  selectable in the Boot action, or referenced via the manual URL overrides.
* **`${fetch}`** in the kernel cmdline is replaced by the squashfs URL (the
  default cmdline `boot=live ${fetch} ip=dhcp` yields
  `boot=live fetch=http://…/bootfiles/root.squashfs ip=dhcp`).
* **`${controller}`** is an iPXE variable set by the `set controller` line from
  the *Controller URL* field (or `APP_URL`).
* Optional toggles additionally capture/echo the MAC into `${node-mac}` and add
  `pve_mac=` to the kernel cmdline — off by default.

Options are stored in the `pxe.boot_config` setting; files live in `storage/bootfiles/`.

### Client agent (inside the node image)

iPXE cannot POST JSON, so the hardware report is done by an agent you add to
your provisioning image **once**. Two variants are served by the panel and
downloadable from the Boot tab:

**`pve-agent.py`** — recommended, Python (standard library only). It finds its
own MAC (the interface that routes to the controller; loopback and
container/virtual NICs are skipped, Proxmox bridges and bonds are kept), reports
MAC/CPU/cores/RAM and then **applies** the returned configuration:

* hostname, the Proxmox way: `/etc/hostname` + `/etc/hosts` (renames all
  occurrences, keeps/updates the management-IP mapping), `/etc/mailname`,
  `/etc/postfix/main.cf` and the history dirs under `/var/lib/rrdcached/db/`
* management network (`/etc/network/interfaces`, `/etc/resolv.conf`) —
  static IP/netmask/gateway, optional VLAN, bridge (`vmbr0`, …)
* Corosync/cluster (`/etc/pve/corosync.conf`, the shared
  `/etc/corosync/authkey`, renaming the node inside it too)
* SSH identity + cluster trust (`/root/.ssh/*`,
  `/etc/pve/priv/authorized_keys`, `/etc/pve/priv/known_hosts`)
* Cluster `/etc/hosts` replication
* NFS storages (`/etc/pve/storage.cfg`)
* PVE certificates + daemons: `pvecm updatecerts --force` and
  `systemctl restart pve-cluster pvedaemon pveproxy pvestatd` (run last, only on
a booted Proxmox system)

```sh
# place it in the image, then run (e.g. from a systemd unit at first boot):
python3 /usr/local/bin/pve-agent.py            # report + apply
python3 /usr/local/bin/pve-agent.py --dry-run  # show what would change
python3 /usr/local/bin/pve-agent.py --report-only
python3 /usr/local/bin/pve-agent.py --root /mnt/target   # installer target
```

Existing files are backed up to `*.pve-agent.bak` before being overwritten, and
every step is independent (a failure is reported, the rest still runs). It is
idempotent.

**One-line install on the node** — installs `pve-agent.py`, the systemd unit and
`/etc/pve-agent.conf` (pointed at this controller), then **enables** the service
but leaves it **stopped**:

```sh
curl -fsSL http://<controller>/agent/install.sh | sh
# ... start it when you're ready:
systemctl start pve-agent.service
```

Or install the parts manually. Grab the unit + config template from the
Boot tab or from `/agent/pve-agent.service` and `/agent/pve-agent.conf`:

```sh
install -m 0755 pve-agent.py      /usr/local/bin/pve-agent.py
install -m 0644 pve-agent.service /etc/systemd/system/pve-agent.service
install -m 0644 pve-agent.conf    /etc/pve-agent.conf
systemctl daemon-reload
systemctl enable --now pve-agent.service
```

The unit is `Type=simple` and runs `pve-agent.py` with no arguments. The agent
**opens a persistent WebSocket** to the controller and waits for configuration
pushes (it never polls). It reconnects automatically with exponential backoff
and only re-applies when the pushed config changes. Pass `--once` (or set
`PVE_ONCE=true`) for a single HTTP report, or `--http` to force the legacy HTTP
polling loop. The agent **auto-loads `pve-agent.conf`** — no need to export
anything. It is searched in this order (first hit wins): `--conf PATH` (or
`PVE_CONF`), `/etc/pve-agent.conf`, then `pve-agent.conf` / `agent.conf` /
`pve.conf` next to the script or in the current directory. So dropping the file
next to `agent.py` is enough to run it manually.

Supported keys (precedence: command line → real environment → this file → kernel
cmdline):

| Key | Meaning |
| --- | --- |
| `PVE_CTRL` | Controller base URL (e.g. `http://192.168.189.249:8080`) |
| `PVE_WS_URL` | Agent WebSocket URL (installer fills it in; default = `PVE_CTRL` host/port + `/agent`) |
| `PVE_API_TOKEN` | Shared API token (if set under Settings → api.token) |
| `PVE_IFACE` | Force a NIC instead of auto-detecting |
| `PVE_MAC` | Force a MAC instead of reading it from the NIC |
| `PVE_ROOT` | Target root (use a mount point from an installer, e.g. `/mnt`) |
| `PVE_REPORT_ONLY` | `true` = report only, apply nothing |
| `PVE_ONCE` | `true` = run a single HTTP report and exit (else the agent stays connected) |
| `PVE_HTTP` | `true` = use HTTP polling instead of the WebSocket |
| `PVE_INTERVAL` | poll interval in seconds for `--http` (default 5) |
| `PVE_DRY_RUN` | `true` = simulate, write nothing |
| `PVE_TIMEOUT` | HTTP/socket timeout in seconds |

**`pve-report.sh`** — minimal POSIX shell. Reports the hardware and saves the
returned config to `/run/pve-config.json`, but applies nothing.

```sh
curl -fsSL http://<controller>/agent/pve-report.sh | sh
```

---

## Security

Security is embedded in the framework layer, not left to individual endpoints:

- **SQL injection** — every value is a bound parameter. Raw identifiers
  (table/column names) must pass `quote_identifier()`, which rejects anything
  that is not a simple name. `sqlite3` also rejects stacked statements.
- **XSS** — Jinja2 autoescaping is on for all HTML templates. Free-form input
  goes through `Sanitizer` helpers where markup is not wanted, and output is
  escaped automatically. A strict Content-Security-Policy is sent.
- **Command injection** — `Shell.run()` never uses a shell, requires an
  argument list, validates every argument and only executes allow-listed
  binaries.
- **Authentication** — passwords are hashed with memory-hard `scrypt` and a
  random per-user salt. Sessions are stored server-side (only a SHA-256 hash of
  the token is persisted), rotate on login (fixation protection), and are
  revoked on logout. Login is throttled per username+IP and returns a single
  generic error (no user enumeration).
- **CSRF** — a session-bound token is required for every unsafe request on the
  `web` group (header `X-CSRF-Token` or `_token` form field).
- **Headers** — `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy` and CSP are set on every response.

---

## Front end

- **Tailwind CSS** and **Font Awesome** (CDN) for styling and icons.
- **HTMX** for navigation: sidebar and content links use `hx-get` with
  `hx-target="#app-content"` and `hx-push-url`, so pages swap **without a full
  reload** (SPA-like behaviour). Page templates extend a `layout` variable, so
  the same template renders either the full shell or just the content fragment
  depending on the `HX-Request` header.

For production you should ship compiled Tailwind CSS instead of the CDN build
and tighten the CSP accordingly.

---

## Configuration

All configuration lives in `.env` (see `.env.example`). Highlights:

| Key | Meaning |
| --- | --- |
| `APP_KEY` | Secret key (generated by `key:generate`) |
| `APP_ENV`, `APP_DEBUG` | Environment + debug error pages |
| `APP_HOST`, `APP_PORT`, `APP_URL` | Server binding and public URL |
| `WS_PATH` | Agent WebSocket path on the HTTP port (default `/agent`) |
| `DB_PATH` | SQLite file location |
| `SESSION_LIFETIME`, `SESSION_SECURE` | Session cookie behaviour |
| `LOGIN_MAX_ATTEMPTS`, `LOGIN_DECAY_SECONDS` | Login throttling |
| `PXE_NEXT_SERVER`, `PXE_BOOT_FILE` | Defaults for PXE options 66/67 |

> A database migration runs automatically on startup; you can also run it
> manually with `migrate`.

---

## Status / next steps

This is the working scaffold: framework core, authentication, the panel
(dashboard, nodes, pending nodes, storage, clusters, boot, settings), the
boot-report API, automatic Corosync config generation, NFS storage management,
a configurable iPXE boot script and a Python provisioning agent that applies
hostname, network, Corosync and storages. Still to build:

- Creating VMs on the node from the stored VM definitions (`qm create`).
- Per-node boot profiles and a provisioning progress UI.
