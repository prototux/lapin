#!/usr/bin/env python3
"""Compares speech recognition on a real 8-channel recording: raw microphone
vs the engine's processed beam (monitor stream) under several settings.

    python3 replay_asr.py room.raw STT_URL [config-json ...]
"""
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import wave
import io

import numpy as np
import requests

raw_path, stt = sys.argv[1], sys.argv[2]
configs = sys.argv[3:] or ['{}']
SATD = os.path.join(os.path.dirname(__file__), "..", "satd")

x = np.fromfile(raw_path, dtype="<i2").reshape(-1, 8)
mic = x[::3, 0].astype(np.float32)          # crude 48k -> 16k (fine for a reference)


def wav(pcm16):
    b = io.BytesIO()
    w = wave.open(b, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm16.astype("<i2").tobytes())
    w.close()
    return b.getvalue()


def asr(pcm16):
    r = requests.post(stt + "/audio/transcriptions", headers={"Authorization": "Bearer " + os.environ.get("STT_TOKEN", "")},
                      files={"file": ("a.wav", wav(pcm16), "audio/wav")}, data={"model": "parakeet-tdt-0.6b"})
    return r.json().get("text", "")


# speech segments from the raw mic energy
fr = mic[:len(mic) // 320 * 320].reshape(-1, 320)
db = 10 * np.log10((fr ** 2).mean(1) + 1)
thr = np.percentile(db, 20) + 12
act = db > thr
segs, start, gap = [], None, 0
for i, a in enumerate(act):
    if a:
        if start is None:
            start = i
        gap = 0
    elif start is not None:
        gap += 1
        if gap > 40:
            if i - gap - start > 15:
                segs.append((max(0, start - 15), i - gap + 15))
            start = None
if start is not None:
    segs.append((start, len(act)))
print("segments:", [(round(a * 0.02, 1), round(b * 0.02, 1)) for a, b in segs])


def engine_monitor(cfg):
    tmp = tempfile.mkdtemp()
    sock = os.path.join(tmp, "e.sock")
    p = subprocess.Popen([SATD, "-i", raw_path, "-n", "-s", sock, "-q"], stderr=subprocess.DEVNULL)
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock)

    def send(o):
        b = json.dumps(o).encode()
        s.sendall(struct.pack("<IB", len(b) + 1, 1) + b)
    send(dict(json.loads(cfg), cmd="config", quiet=True))
    send({"cmd": "subscribe", "monitor": True})
    out, buf = bytearray(), b""
    s.settimeout(1)
    while True:
        try:
            d = s.recv(65536)
        except socket.timeout:
            if p.poll() is not None:
                break
            continue
        if not d:
            break
        buf += d
        while len(buf) >= 5:
            ln, t = struct.unpack("<IB", buf[:5])
            if len(buf) < 4 + ln:
                break
            if t == 4:
                out += buf[5:4 + ln]
            buf = buf[4 + ln:]
    p.wait()
    return np.frombuffer(bytes(out), dtype="<i2").astype(np.float32)


print("\n== raw MIC1")
for a, b in segs:
    print("  %5.1f-%5.1fs: %s" % (a * 0.02, b * 0.02, asr(mic[a * 320:b * 320])))
for cfg in configs:
    mon = engine_monitor(cfg)
    lag = 512           # STFT latency of the beam
    print("\n== engine", cfg, "(%.0fs)" % (len(mon) / 16000))
    for a, b in segs:
        seg = mon[a * 320 + lag:b * 320 + lag]
        if len(seg):
            g = 3000 / (np.abs(seg).max() + 1)        # normalize level (AGC off in the monitor)
            print("  %5.1f-%5.1fs: %s" % (a * 0.02, b * 0.02, asr(seg * min(g, 30))))
