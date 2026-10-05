"""Wake word recordings (enrollment samples) collected from the satellites,
kept so that devices without an enrollment of their own (ESP32 satellites)
can use the same personal wake word."""

import base64
import hashlib
import os
import re
import threading

MAX_PER_KEYWORD = 30
MAX_BYTES = 200 * 1024          # a 2 s 16 kHz mono sample is 64 KB


class WakeWords:
    def __init__(self, app):
        self.dir = os.path.join(app.data_dir, "wakewords")
        self.lock = threading.Lock()
        self.hold = set()           # device ids not sent the recordings for now

    def store(self, keyword, wav):
        """Keeps a sample; True if it is new."""
        if not re.fullmatch(r"[a-z0-9_]{1,40}", keyword or "") or not wav[:4] == b"RIFF" or len(wav) > MAX_BYTES:
            return False
        d = os.path.join(self.dir, keyword)
        name = hashlib.sha1(wav).hexdigest()[:16] + ".wav"
        path = os.path.join(d, name)
        with self.lock:
            if os.path.exists(path):
                return False
            os.makedirs(d, exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(wav)
            os.replace(path + ".tmp", path)
            files = sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".wav")), key=os.path.getmtime)
            for old in files[:-MAX_PER_KEYWORD]:
                os.remove(old)
        return True

    def all(self, keyword=None):
        out = []
        if not os.path.isdir(self.dir):
            return out
        for kw in sorted(os.listdir(self.dir)):
            if keyword and kw != keyword:
                continue
            d = os.path.join(self.dir, kw)
            for name in sorted(os.listdir(d)):
                if name.endswith(".wav"):
                    with open(os.path.join(d, name), "rb") as f:
                        out.append({"keyword": kw, "name": name, "wav_b64": base64.b64encode(f.read()).decode()})
        return out
