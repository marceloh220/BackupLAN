#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/marcelinoServer"
APP_SCRIPT="${APP_DIR}/marcelinoServer.py"
SERVICE_NAME="marcelinoServer.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
CONFIG_PATH="/etc/default/marcelinoServer"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This installer supports Linux only." >&2
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this installer as root: sudo bash install_marcelinoServer.sh" >&2
    exit 1
fi

for source_file in "${SCRIPT_DIR}/marcelinoServer.py" "${SCRIPT_DIR}/marcelinoServer.service"; do
    if [[ ! -f "${source_file}" ]]; then
        echo "Required file not found: ${source_file}" >&2
        exit 1
    fi
done

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl was not found; this server does not appear to use systemd." >&2
    exit 1
fi

if ! /usr/bin/python3 -c 'import psutil' >/dev/null 2>&1; then
    echo "Python module psutil is missing. Install it for /usr/bin/python3 before continuing." >&2
    echo "On Debian/Ubuntu, try: apt install python3-psutil" >&2
    exit 1
fi

install -d -o root -g root -m 0755 "${APP_DIR}"
install -o root -g root -m 0644 "${SCRIPT_DIR}/marcelinoServer.py" "${APP_SCRIPT}"
install -o root -g root -m 0644 "${SCRIPT_DIR}/marcelinoServer.service" "${SERVICE_PATH}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
    install -d -o root -g root -m 0755 /etc/default
    cat > "${CONFIG_PATH}" <<EOF
MARCELINOSRV_SCRIPT=${APP_SCRIPT}
MARCELINOSRV_STATUS=/srv/marcelo/.marcelinoSrv.json
MARCELINOSRV_INTERVAL=5
MARCELINOSRV_FILE_MODE=0644
EOF
    chown root:root "${CONFIG_PATH}"
    chmod 0644 "${CONFIG_PATH}"
else
    echo "Preserving existing configuration: ${CONFIG_PATH}"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "${SERVICE_PATH}"
fi

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

if ! command -v smartctl >/dev/null 2>&1; then
    echo "Warning: smartctl is missing; SMART disk data will be unavailable." >&2
    echo "Install smartmontools to enable SMART reporting." >&2
fi

systemctl --no-pager --full status "${SERVICE_NAME}"
