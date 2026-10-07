#!/bin/sh
# Launcher of the Flatpak: the app runs from its sources in /app/share.
export PYTHONPATH="/app/share/lapin-desktop${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m lapin_desktop "$@"
