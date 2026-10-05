#!/bin/sh
# One-command flasher for the Korvo satellite (Linux / macOS). See flash.py --help.
# On Windows: python flash.py
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
        exec "$py" flash.py "$@"
    fi
done
echo "Python 3.8 or newer is needed (https://www.python.org/downloads/)." >&2
exit 1
