#!/bin/sh
# Starts the Lapin server from wherever this folder is. The first run creates
# .venv here and installs requirements.txt; settings, the database and the
# wake word recordings live in ./data (override with DATA=/some/dir).
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
VENV="$DIR/.venv"
if [ ! -x "$VENV/bin/python" ] || [ "$DIR/requirements.txt" -nt "$VENV/.deps-ok" ]; then
    [ -x "$VENV/bin/python" ] || "${PYTHON:-python3}" -m venv "$VENV"
    "$VENV/bin/python" -m pip install -q --upgrade pip
    "$VENV/bin/python" -m pip install -q -r "$DIR/requirements.txt"
    touch "$VENV/.deps-ok"
fi
cd "$DIR"
exec "$VENV/bin/python" -m assistant --data "${DATA:-$DIR/data}" "$@"
