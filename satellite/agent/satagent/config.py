"""Persistent settings of the satellite (one JSON file)."""

import copy
import json
import os
import secrets
import threading
import uuid

# Engine settings pushed to satd at every connection (names: see engine.c).
ENGINE_DEFAULTS = {
    "volume": 60, "speaker_muted": False, "mic_muted": False,
    "aec_enabled": True, "aec_tail_ms": 96, "res_enabled": True, "res_strength": 1.0, "res_floor_db": -10,
    "bf_mode": "superdirective", "ns_enabled": True, "ns_floor_db": -8,
    "agc_enabled": True, "agc_target_db": -20, "agc_max_gain_db": 24,
    "capture_gain_db": 0, "mic_gain_db": [0, 0, 0, 0, 0, 0],
    "kws_enabled": True, "kws_threshold": 0.38, "kws_min_matches": 0, "kws_neg_margin": 0.03,
    "kws_barge_in": True, "kws_playback_margin": 0.08, "kws_tts_threshold": 0.44,
    "vad_threshold_db": 6, "eos_silence_ms": 1200, "listen_timeout_ms": 6000,
    "max_listen_ms": 15000, "preroll_ms": 1500,
    "earcons": True, "earcon_wake": True, "earcon_end": True,
    "duck_db": -18, "listen_duck_db": -40, "tts_gain_db": 0, "media_gain_db": 0, "alarm_gain_db": 0,
    "earcon_gain_db": -4,
    "eq_low_db": 0, "eq_mid_db": 0, "eq_high_db": 0, "hpf_hz": 90,
    "drc_threshold_db": -14, "drc_ratio": 3, "limiter_db": -1,
}

DEFAULTS = {
    "device_id": "",
    "token": "",
    "name": "Satellite",
    "room": "",
    "server_url": "ws://assistant.local:8765/v1/device",
    "engine_socket": "/run/satellite/engine.sock",
    "webui": {"host": "0.0.0.0", "port": 8080, "password": ""},
    "wakeword": {"auto_threshold": True},
    "button": {"enabled": True, "long_press_ms": 1500},
    "engine": ENGINE_DEFAULTS,
    "plugins": {},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "config.json")
        self.wakeword_dir = os.path.join(data_dir, "wakewords")
        self.lock = threading.RLock()
        os.makedirs(self.wakeword_dir, exist_ok=True)
        try:
            with open(self.path) as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        self.data = _merge(DEFAULTS, stored)
        if not self.data["device_id"]:
            self.data["device_id"] = "sat-" + uuid.uuid4().hex[:12]
        if not self.data["token"]:
            self.data["token"] = secrets.token_urlsafe(24)
        self.save()

    def __getitem__(self, key):
        with self.lock:
            return self.data[key]

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def update(self, changes):
        with self.lock:
            self.data = _merge(self.data, changes)
            self.save()

    def engine(self):
        with self.lock:
            return dict(self.data["engine"])

    def plugin(self, name):
        with self.lock:
            return dict(self.data["plugins"].get(name, {}))

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def public(self):
        """Settings without the secrets, for the web UI."""
        with self.lock:
            d = copy.deepcopy(self.data)
        d["token"] = "********"
        if d["webui"].get("password"):
            d["webui"]["password"] = "********"
        return d
