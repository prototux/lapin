"""Persistent settings of the desktop app (one JSON file in the platform's
config directory)."""

import copy
import json
import os
import secrets
import socket
import threading
import uuid

from PySide6.QtCore import QStandardPaths

DEFAULTS = {
    "device_id": "",
    "token": "",
    "server_url": "ws://assistant.local:8765/v1/device",
    "name": "",                 # empty: the host name
    "owner": "",                # household user this computer belongs to
    "room": "",
    "hotkey": "Ctrl+Alt+Space",  # Qt portable key sequence
    "autostart": False,
    "speak_typed": False,       # typed requests: answer in text only (typing is to stay quiet)
    "language": "auto",         # auto | fr | en (UI strings)
    "volume": 80,               # the app's own output gain, 0-100 (server "set")
    "mic_muted": False,
    "earcons": True,
    "input_device": "",         # sounddevice name; empty: system default
    "output_device": "",
    "screenshot_dir": "",       # empty: the Pictures folder
}


def default_dir():
    d = os.environ.get("LAPIN_CONFIG_DIR")
    if d:
        return d
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return d or os.path.join(os.path.expanduser("~"), ".lapin-desktop")


class Config:
    def __init__(self, directory=None):
        self.dir = directory or default_dir()
        self.path = os.path.join(self.dir, "config.json")
        self.lock = threading.RLock()
        os.makedirs(self.dir, exist_ok=True)
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        self.data = copy.deepcopy(DEFAULTS)
        self.data.update({k: v for k, v in stored.items() if k in DEFAULTS})
        changed = False
        if not self.data["device_id"]:
            self.data["device_id"] = "desktop-" + uuid.uuid4().hex
            changed = True
        if not self.data["token"]:
            self.data["token"] = secrets.token_urlsafe(32)
            changed = True
        if changed or not os.path.exists(self.path):
            self.save()

    def __getitem__(self, key):
        with self.lock:
            return self.data[key]

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def name(self):
        return self.get("name") or socket.gethostname().split(".")[0] or "Computer"

    def update(self, changes):
        with self.lock:
            self.data.update({k: v for k, v in changes.items() if k in DEFAULTS})
            self.save()

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            if os.name != "nt":
                os.chmod(tmp, 0o600)    # holds the device token
            os.replace(tmp, self.path)
