#!/bin/sh
# Installs the satellite on the ReSpeaker Core v2 (Debian 13 image). Run on
# the board, from the directory holding engine/ agent/ systemd/:
#     sudo ./install.sh [ws://server:8765/v1/device]
set -eu
cd "$(dirname "$0")"
USER_NAME=${SATELLITE_USER:-respeaker}

if [ "$(id -u)" != 0 ]; then echo "run with sudo" >&2; exit 1; fi

echo "== packages"
need=""
for p in build-essential pkg-config libasound2-dev libspeexdsp-dev python3-flask python3-websockets \
         python3-spidev python3-libgpiod gpiod alsa-utils; do
    dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"
done
if [ -n "$need" ]; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need
fi

echo "== engine"
make -C engine -j2 >/dev/null
install -D -m 0755 engine/satd /opt/satellite/bin/satd

echo "== agent"
rm -rf /opt/satellite/agent
mkdir -p /opt/satellite/agent
cp -r agent/satagent /opt/satellite/agent/
find /opt/satellite/agent -name __pycache__ -prune -exec rm -rf {} +

echo "== permissions"
usermod -aG audio,spi,gpio,input,adm "$USER_NAME"
cat > /etc/sudoers.d/satellite <<SUDO
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart satellite-engine, /usr/bin/systemctl restart satellite-agent
SUDO
chmod 0440 /etc/sudoers.d/satellite

echo "== services"
install -m 0644 systemd/satellite-engine.service systemd/satellite-agent.service /etc/systemd/system/
systemctl daemon-reload
if [ "${1:-}" ]; then
    mkdir -p /var/lib/satellite
    python3 - "$1" <<'PY'
import json, os, sys
p = "/var/lib/satellite/config.json"
d = json.load(open(p)) if os.path.exists(p) else {}
d["server_url"] = sys.argv[1]
json.dump(d, open(p, "w"), indent=2)
PY
    chown -R "$USER_NAME" /var/lib/satellite
fi
systemctl enable satellite-engine satellite-agent >/dev/null 2>&1
systemctl restart satellite-engine
sleep 1
systemctl restart satellite-agent
echo "done: web UI on http://$(hostname -I | awk '{print $1}'):8080"
