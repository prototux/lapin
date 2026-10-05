"""Server settings: defaults, persisted overrides (data/settings.json)."""

import copy
import json
import os
import threading

DEFAULTS = {
    "server": {"name": "Assistant", "gateway_host": "0.0.0.0", "gateway_port": 8765,
               "web_host": "0.0.0.0", "web_port": 8090, "web_password": "",
               "auto_approve": False, "language": "en"},
    "stt": {"url": "http://localhost:5092/v1", "token": "", "model": "parakeet-tdt-0.6b",
            "timeout": 20},
    "tts": {"url": "http://localhost:8091/v1", "token": "",
            "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "voice": "serena", "voice_fr": "", "stream": True,
            "instructions": ("Speak naturally and confidently, like a helpful modern voice assistant. Keep the "
                             "delivery clear and conversational, in an even, composed tone. Only speak the words "
                             "provided in the text."),
            "cache_phrases": True, "timeout": 60},
    "llm": {"url": "http://localhost:8000/v1", "token": "",
            "model": "qwen3.8-27b", "temperature": 0.6, "max_tokens": 600, "disable_thinking": True,
            "timeout": 90, "max_tool_rounds": 5, "history_turns": 8,
            "voice_max_sentences": 4, "voice_max_chars": 420},
    "assistant": {
        "name": "Lapin",
        "persona": ("You are Lapin, a friendly and concise home voice assistant. You run on the "
                    "family's own server. Be warm, natural and brief."),
        "household": "", "location": "", "timezone": "",
        "language": "auto",         # auto (follow each request), en or fr
        "default_language": "en",   # for announcements, alarms, reminders
        "follow_up": True, "follow_up_ms": 6000, "max_misses": 3,
        "conversation_timeout_s": 300,
    },
    "wake": {
        "verify": True, "phrases": ["hey lapin"], "aliases": {}, "threshold": 0.75,
        "arbitration_ms": 180, "learn_aliases": True,
    },
    "endpoint": {"fast_silence_ms": 800, "slow_silence_ms": 1600, "max_turn_s": 20,
                 "partials": True, "partial_interval_ms": 900, "semantic": True},
    "enhance": {"denoise": False},
    "privacy": {"store_audio": False, "retention_days": 30, "store_transcripts": True},
    "quiet_hours": {"enabled": False, "start": "22:30", "end": "07:00"},
    "media": {"stations": [
        {"name": "FIP", "url": "https://icecast.radiofrance.fr/fip-hifi.aac"},
        {"name": "France Inter", "url": "https://icecast.radiofrance.fr/franceinter-hifi.aac"},
        {"name": "SomaFM Groove Salad", "url": "https://ice1.somafm.com/groovesalad-128-mp3"},
        {"name": "BBC World Service", "url": "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service"},
    ], "default_volume_db": -6},
    "skills": {"disabled": []},
    "homeassistant": {"url": "", "token": ""},
    "search": {"url": "", "engines": ["duck", "bing", "yandex"], "results": 5},
    "channels": {},     # per channel module: {"enabled": bool, "users": {external id: user}, ...}
    "guardrails": {"rate_per_minute": 20},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("aliases", "allowed", "users"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    SECRET_PATHS = [("llm", "token"), ("stt", "token"), ("tts", "token"), ("homeassistant", "token"),
                    ("server", "web_password")]

    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "settings.json")
        self.lock = threading.RLock()
        self.listeners = []
        try:
            with open(self.path) as f:
                self.overrides = json.load(f)
        except (OSError, ValueError):
            self.overrides = {}
        self.data = _merge(DEFAULTS, self.overrides)

    def __getitem__(self, section):
        with self.lock:
            return self.data[section]

    def get(self, section, key, default=None):
        with self.lock:
            return self.data.get(section, {}).get(key, default)

    def update(self, changes):
        """Deep-merges `changes` into the overrides and saves."""
        with self.lock:
            self.overrides = _merge(self.overrides, changes)
            self.data = _merge(DEFAULTS, self.overrides)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.overrides, f, indent=2)
            os.replace(tmp, self.path)
        for fn in list(self.listeners):
            try:
                fn(changes)
            except Exception:
                pass

    def public(self):
        with self.lock:
            d = copy.deepcopy(self.data)
        for sec, key in self.SECRET_PATHS:
            if d.get(sec, {}).get(key):
                d[sec][key] = "********"
        for ch in d.get("channels", {}).values():
            if isinstance(ch, dict) and ch.get("token"):
                ch["token"] = "********"
        return d

    def clean_update(self, changes):
        """Like update() but ignores masked secrets sent back by the UI."""
        changes = copy.deepcopy(changes)
        for sec, key in self.SECRET_PATHS:
            if changes.get(sec, {}).get(key) == "********":
                del changes[sec][key]
        self.update(changes)
