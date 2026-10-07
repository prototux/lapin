#!/bin/sh
# Builds the lapin-satellite Debian package: satd, the agent, the systemd
# units and the sudoers rule, the same files install.sh puts in place.
# Run it on Debian 13 for the target architecture: on the board itself, or in
# an armhf container (that is what the release workflow does):
#     sudo apt install build-essential pkg-config libasound2-dev libspeexdsp-dev
#     satellite/packaging/build-deb.sh [output dir, default satellite/dist]
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
SAT=$(dirname "$HERE")
OUT=${1:-$SAT/dist}
VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$SAT/agent/satagent/__init__.py" | tr - '~')
ARCH=$(dpkg --print-architecture)
STAGE=$(mktemp -d)
chmod 0755 "$STAGE"
trap 'rm -rf "$STAGE"' EXIT

echo "== satd ($ARCH)"
make -C "$SAT/engine" clean >/dev/null
make -C "$SAT/engine" -j"$(nproc)" >/dev/null
install -D -m 0755 "$SAT/engine/satd" "$STAGE/opt/satellite/bin/satd"
make -C "$SAT/engine" clean >/dev/null

echo "== agent"
mkdir -p "$STAGE/opt/satellite/agent"
cp -r "$SAT/agent/satagent" "$STAGE/opt/satellite/agent/"
find "$STAGE/opt/satellite/agent" -name __pycache__ -prune -exec rm -rf {} +

echo "== units, sudoers"
install -D -m 0644 -t "$STAGE/usr/lib/systemd/system" \
    "$SAT/systemd/satellite-engine.service" "$SAT/systemd/satellite-agent.service"
# lets the agent's web page restart the two services (same rule as install.sh)
install -d -m 0750 "$STAGE/etc/sudoers.d"
printf 'respeaker ALL=(root) NOPASSWD: /usr/bin/systemctl restart satellite-engine, /usr/bin/systemctl restart satellite-agent\n' \
    >"$STAGE/etc/sudoers.d/lapin-satellite"
chmod 0440 "$STAGE/etc/sudoers.d/lapin-satellite"
install -D -m 0644 "$SAT/../LICENSE" "$STAGE/usr/share/doc/lapin-satellite/copyright"

mkdir -p "$STAGE/DEBIAN"
install -m 0755 "$HERE/debian/postinst" "$HERE/debian/prerm" "$HERE/debian/postrm" "$STAGE/DEBIAN/"
echo /etc/sudoers.d/lapin-satellite >"$STAGE/DEBIAN/conffiles"
cat >"$STAGE/DEBIAN/control" <<CONTROL
Package: lapin-satellite
Version: $VERSION
Architecture: $ARCH
Maintainer: Lapin <https://github.com/prototux/lapin>
Installed-Size: $(du -sk "$STAGE" | cut -f1)
Depends: libc6, libasound2t64 | libasound2, libspeexdsp1, python3 (>= 3.11), python3-flask, python3-websockets, python3-spidev, python3-libgpiod, gpiod, alsa-utils, sudo, adduser
Section: sound
Priority: optional
Homepage: https://github.com/prototux/lapin
Description: Lapin voice assistant satellite for the ReSpeaker Core v2
 The room speaker of the Lapin voice assistant: satd, the real-time audio
 engine (echo cancellation, beamforming, noise suppression, wake word,
 playback mixer), and satagent, the link to the Lapin server with the LED
 ring, the button and a web page on port 8080.
CONTROL

mkdir -p "$OUT"
DEB="$OUT/lapin-satellite_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB" >/dev/null
echo "$DEB"
