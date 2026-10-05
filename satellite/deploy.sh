#!/bin/sh
# Copies the satellite to a board and installs it there.
#     ./deploy.sh respeaker@192.168.1.50 [ws://server:8765/v1/device]
# Asks for the board user's password (sudo) unless SUDO_PASS is set.
# Extra ssh options (e.g. a key) can be given in SSH_OPTS.
set -eu
cd "$(dirname "$0")"
HOST=${1:?usage: $0 user@host [server_url]}
SERVER=${2:-}
SSH="ssh ${SSH_OPTS:-}"
rsync -a --delete --exclude '*.o' --exclude engine/satd --exclude 'engine/tests/kwstest' \
      --exclude __pycache__ -e "$SSH" ./ "$HOST:satellite-src/"
if [ -z "${SUDO_PASS:-}" ]; then
    printf "sudo password on %s: " "$HOST"; stty -echo; read -r SUDO_PASS; stty echo; echo
fi
echo "$SUDO_PASS" | $SSH "$HOST" "sudo -S -p '' sh satellite-src/install.sh $SERVER"
