# iPXE-PveController

A web panel + API that **PXE-boots Proxmox VE nodes** and **auto-configures**
them (nodename, IP, networking, VMs, cluster, Corosync, storage) based on the
**MAC address** of each node's provisioning NIC.

Built as a small, Laravel-style MVC framework in Python (Werkzeug + Jinja2),
with Tailwind CSS, Font Awesome and HTMX on the front end.

---

## Quick start

```bash
# 1. Create a virtualenv and install dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Create your .env and generate an application key
python3 main.py key:generate

# 3. Start everything: the server starts and you get a console
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
3. The provisioning agent on the node POSTs its hardware inventory to
   `POST /api/boot/report`:

   ```json
   {
     "mac_address": "aa:bb:cc:dd:ee:ff",
     "cpu_model": "AMD EPYC 7402P 24-Core Processor",
     "cpu_count": 24,
     "ram_gb": 128,
     "ip_address": "10.0.0.10"
   }
   ```

4. The controller looks the node up **by MAC**, logs the boot event, refreshes
   the stored hardware data, and returns the configuration the node should
   apply (name, IP, networking, cluster, Corosync, storage, VMs).

Unknown MACs are recorded as *unregistered* boot events so you can map them in
the panel.

### API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check |
| `POST` | `/api/boot/report` | Node reports hardware + gets its config |
| `GET` | `/api/boot/config/<mac>` | Fetch a node's config by MAC |
| `GET` | `/api/nodes` | Read-only list of configured nodes |
| `GET` | `/boot.ipxe` | iPXE boot script |

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
| `DB_PATH` | SQLite file location |
| `SESSION_LIFETIME`, `SESSION_SECURE` | Session cookie behaviour |
| `LOGIN_MAX_ATTEMPTS`, `LOGIN_DECAY_SECONDS` | Login throttling |
| `PXE_NEXT_SERVER`, `PXE_BOOT_FILE` | Defaults for PXE options 66/67 |

> A database migration runs automatically on startup; you can also run it
> manually with `migrate`.

---

## Status / next steps

This is the working scaffold: framework core, authentication, the panel
(dashboard, nodes, clusters, settings), the boot-report API and automatic
Corosync config generation. Still to build:

- Generating Proxmox answer files / cloud-init from the stored node config.
- Applying the generated cluster/storage/VM definitions during provisioning.
- Per-node boot profiles and a provisioning progress UI.
