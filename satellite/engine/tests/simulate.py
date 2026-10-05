#!/usr/bin/env python3
"""
Offline test of satd's capture DSP with a synthetic room recording.

Builds an 8-channel 48 kHz raw file (6 mics + 2 loopback) with:
  - a talker (speech file) at a known bearing, free-field delays per mic,
  - an optional interferer (noise from another bearing),
  - optional "music" played by the device: in the loopback channels and,
    through a short echo path, in every microphone,
  - a little independent mic noise,
then runs `satd -i file -n` and reads its meters / events from the socket.

    python3 simulate.py speech.wav --bearing 120 [--interferer 300] [--echo]

Needs numpy. speech.wav: mono 16-bit, any rate.
"""
import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np

MICS = np.array([(-23.2, 40.1), (23.2, 40.1), (46.3, 0.0),
                 (23.2, -40.1), (-23.2, -40.1), (-46.3, 0.0)]) / 1000.0
C = 343.0
FS = 48000


def load(path):
    with wave.open(path) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768
        rate = w.getframerate()
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels())[:, 0]
    if rate != FS:  # FFT resampling
        n = int(round(len(x) * FS / rate))
        X = np.fft.rfft(x)
        Y = np.zeros(n // 2 + 1, complex)
        m = min(len(X), len(Y))
        Y[:m] = X[:m]
        x = np.fft.irfft(Y, n) * n / len(x)
    return x


def delayed(x, delay_s):
    """x delayed by a fractional delay (FFT phase shift, circular, padded)."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / FS)
    return np.fft.irfft(np.fft.rfft(x) * np.exp(-2j * np.pi * f * delay_s), n)


def at_mics(x, bearing):
    th = np.radians(bearing)
    u = np.array([np.sin(th), np.cos(th)])
    taus = -(MICS @ u) / C + 0.001       # +1 ms so every delay is positive
    return np.stack([delayed(x, t) for t in taus])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("speech")
    ap.add_argument("--bearing", type=float, default=120)
    ap.add_argument("--interferer", type=float, default=None)
    ap.add_argument("--echo", action="store_true")
    ap.add_argument("--satd", default=os.path.join(os.path.dirname(__file__), "..", "satd"))
    ap.add_argument("--kws", default=None, help="wake word dir for satd -k")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--background", help="real 8-channel s16le 48 kHz recording to mix the talker into")
    ap.add_argument("--speech-db", type=float, default=0, help="talker gain (dB)")
    ap.add_argument("--config", default="{}", help="engine config JSON sent at start")
    ap.add_argument("--nolink", action="store_true", help="no server: wakes are only logged (all detections)")
    args = ap.parse_args()

    rng = np.random.default_rng(1)
    sp = load(args.speech) * 0.5 * 10 ** (args.speech_db / 20)
    gap = np.zeros(int(1.5 * FS))
    sp = np.concatenate([gap, np.concatenate([np.concatenate([sp, gap])] * args.repeat)])
    n = len(sp)
    mics = at_mics(sp, args.bearing)
    if args.interferer is not None:
        noise = rng.standard_normal(n)
        noise = np.convolve(noise, np.ones(8) / 8, "same") * 0.03   # lowpassed
        mics += at_mics(noise, args.interferer)
    loop = np.zeros((2, n))
    if args.echo:
        t = np.arange(n) / FS
        music = 0.15 * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 330 * t)
                        + 0.3 * rng.standard_normal(n) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t)))
        loop[0] = loop[1] = music
        for m in range(6):
            ir = rng.standard_normal(1200) * np.exp(-np.arange(1200) / 200) * 0.25
            ir[40 + m * 3] += 1.0                                     # direct path ~1 ms
            mics[m] += np.convolve(music, ir)[:n] * 0.6
    mics += rng.standard_normal(mics.shape) * 10 ** (-65 / 20)
    data = np.vstack([mics, loop]).T
    if args.background:
        bg = np.fromfile(args.background, dtype="<i2").reshape(-1, 8).astype(np.float64) / 32768
        lead = int(4 * FS)                  # let the echo models train first
        total = max(len(bg), lead + len(data))
        out = np.zeros((total, 8))
        out[:len(bg)] += bg
        out[lead:lead + len(data), :6] += data[:, :6]
        data = out
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")

    tmp = tempfile.mkdtemp()
    raw = os.path.join(tmp, "in.raw")
    pcm.tofile(raw)
    sock = os.path.join(tmp, "e.sock")
    cmd = [args.satd, "-i", raw, "-n", "-s", sock] + (["-v"] if os.environ.get("SATD_V") else [])
    if args.kws:
        cmd += ["-k", args.kws]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock)

    def send(obj):
        b = json.dumps(obj).encode()
        s.sendall(struct.pack("<IB", len(b) + 1, 1) + b)

    send({"cmd": "subscribe", "meters": True, "audio": True})
    send({"cmd": "link", "up": not args.nolink})
    send(dict(json.loads(args.config), cmd="config", quiet=True))
    buf = b""
    tracks, events, uplink = [], [], 0
    s.settimeout(1.0)
    while True:
        try:
            d = s.recv(65536)
        except socket.timeout:
            if proc.poll() is not None:
                break
            continue
        if not d:
            break
        buf += d
        while len(buf) >= 5:
            ln, typ = struct.unpack("<IB", buf[:5])
            if len(buf) < 4 + ln:
                break
            payload = buf[5:4 + ln]
            buf = buf[4 + ln:]
            if typ == 1:
                ev = json.loads(payload)
                if ev["event"] == "meters":
                    tracks.append(ev)
                elif ev["event"] not in ("hello", "status"):
                    events.append(ev)
            elif typ == 2:
                uplink += len(payload) // 2
    proc.wait()
    err = proc.stderr.read()

    speech = [m for m in tracks if m["speech"]]
    print("meters: %d, speech frames: %d" % (len(tracks), len(speech)))
    if speech:
        doas = np.array([m["doa"] for m in speech if m["doa_conf"] > 0.1])
        errs = np.abs((doas - args.bearing + 180) % 360 - 180)
        print("DOA during speech: median error %.1f deg (n=%d)" % (np.median(errs), len(doas)))
        tr = np.array([m["track"] for m in speech])
        terr = np.abs((tr - args.bearing + 180) % 360 - 180)
        print("tracker: median error %.1f deg, last %.1f" % (np.median(terr[len(terr) // 2:]), tr[-1]))
    erle = [m["erle_db"] for m in tracks if m["erle_db"] != 0]
    if erle:
        print("ERLE (far-end only): last %.1f dB, max %.1f dB" % (erle[-1], max(erle)))
    res = [m.get("res_db", 0) for m in tracks if m.get("res_db")]
    if res:
        print("residual echo suppression: last %.1f dB, max %.1f dB; total echo reduction ~%.1f dB"
              % (res[-1], max(res), res[-1] + (erle[-1] if erle else 0)))
    cpu = [m["cpu"]["frame_us"] for m in tracks]
    print("CPU per 16 ms frame: mean %.0f us, max %.0f us" % (np.mean(cpu), max(m["cpu"]["max_us"] for m in tracks)))
    st = np.array([m["static"] for m in tracks[-5:]]).mean(axis=0)
    if st.max() > 0.3:
        print("static interferer map peak at %d deg (%.2f)" % (int(np.argmax(st)) * 8, st.max()))
    for e in events:
        print("event:", json.dumps(e))
    print("uplink samples:", uplink)
    for line in err.splitlines():
        if line.startswith("E:") or (os.environ.get("SATD_V") and not line.startswith("D: cmd")):
            print(line)


if __name__ == "__main__":
    main()
