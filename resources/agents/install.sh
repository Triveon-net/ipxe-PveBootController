#!/bin/sh
#
# iPXE-PveController agent installer.
#
#   curl -fsSL http://<controller>/agent/install.sh | sh
#
# Installs pve-agent.py, the systemd unit and /etc/pve-agent.conf, then
# ENABLES pve-agent.service but leaves it STOPPED. Start it when you're ready.
#
set -eu

# The controller URL is injected by the panel when this script is downloaded.
CONTROLLER="${CONTROLLER:-__CONTROLLER__}"
CONTROLLER="${CONTROLLER%/}"
BASE="$CONTROLLER/agent"

BIN=/usr/local/bin/pve-agent.py
UNIT=/etc/systemd/system/pve-agent.service
CONF=/etc/pve-agent.conf

if [ "$(id -u)" -ne 0 ]; then
    echo "install: please run as root" >&2
    exit 1
fi

have() { command -v "$1" >/dev/null 2>&1; }

fetch() {  # url dest
    if have curl; then
        curl -fsSL "$1" -o "$2"
    elif have wget; then
        wget -qO "$2" "$1"
    else
        echo "install: need 'curl' or 'wget'" >&2
        exit 1
    fi
}

echo "install: downloading agent from $CONTROLLER"
fetch "$BASE/pve-agent.py" "$BIN"
fetch "$BASE/pve-agent.service" "$UNIT"
chmod 0755 "$BIN"
chmod 0644 "$UNIT"

if [ -f "$CONF" ]; then
    echo "install: keeping existing $CONF"
else
    fetch "$BASE/pve-agent.conf" "$CONF"
    # Point the config at the controller this installer came from.
    if grep -q '^PVE_CTRL=' "$CONF"; then
        sed -i "s#^PVE_CTRL=.*#PVE_CTRL=$CONTROLLER#" "$CONF"
    else
        printf 'PVE_CTRL=%s\n' "$CONTROLLER" >> "$CONF"
    fi
    chmod 0644 "$CONF"
    echo "install: wrote $CONF (PVE_CTRL=$CONTROLLER)"
fi

if have systemctl; then
    systemctl daemon-reload
    systemctl enable pve-agent.service
    echo "install: enabled pve-agent.service (stopped — not started)"
    echo "install: start it with:  systemctl start pve-agent.service"
else
    echo "install: systemd not found; run manually:  python3 $BIN --watch"
fi

echo "install: done"
