#!/usr/bin/env python3
"""End-to-end test of a voice turn through the device protocol, without a
satellite: a simulated device sends a wake (with pre-roll), streams speech in
real time, and records the answer.

    python tests/fake_device.py "What's the weather in Lyon?" [--wake "Hey Lapin"]

The speech is synthesized by the TTS server. Needs the server running
(uses the browser secret from the admin API)."""

import argparse
import json
import sys
import threading
import time
import wave

import numpy as np
import requests
from websockets.sync.client import connect

ap = argparse.ArgumentParser()
ap.add_argument("text")
ap.add_argument("--wake", default="Hey Lapin")
ap.add_argument("--web", default="http://localhost:8090")
ap.add_argument("--voice", default="ryan")
ap.add_argument("--out", default="/tmp/answer.wav")
ap.add_argument("--source", default="kws")
args = ap.parse_args()

tok = requests.get(args.web + "/api/browser_token").json()
settings = requests.get(args.web + "/api/settings").json()


def speech(text):
    r = requests.post(settings["tts"]["url"] + "/audio/speech", json={
        "model": settings["tts"]["model"], "voice": args.voice, "input": text, "response_format": "pcm"},
        timeout=60)
    x = np.frombuffer(r.content[44:] if r.content[:4] == b"RIFF" else r.content, dtype="<i2").astype(np.float32)
    # 24 kHz -> 16 kHz
    n = int(len(x) * 2 / 3)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return y.astype("<i2").tobytes()


wake_pcm = speech(args.wake + ".") if args.source == "kws" else b""
cmd_pcm = speech(args.text)
silence = lambda ms: (np.random.randn(16 * ms) * 30).astype("<i2").tobytes()
preroll = (silence(1500) + wake_pcm)[-1500 * 32:] if wake_pcm else silence(300)

events, audio = [], bytearray()
t0 = time.time()
done = threading.Event()
eot_at = [None]

with connect(tok["url"], max_size=16 * 1024 * 1024) as ws:
    ws.send(json.dumps({"type": "hello", "device_id": "test-device", "token": tok["token"], "kind": "browser",
                        "name": "Test device", "capabilities": {}}))
    welcome = json.loads(ws.recv(timeout=5))
    assert welcome["type"] == "welcome", welcome

    def reader():
        for msg in ws:
            if isinstance(msg, bytes):
                audio.extend(msg[5:])
                continue
            m = json.loads(msg)
            events.append((round(time.time() - t0, 3), m))
            if m["type"] == "eot":
                eot_at[0] = time.time()
            if m["type"] in ("session_end", "cancel"):
                done.set()
                return
    threading.Thread(target=reader, daemon=True).start()

    t0 = time.time()
    ws.send(json.dumps({"type": "wake", "wake_id": 1, "source": args.source, "keyword": "hey_lapin",
                        "score": float(__import__("os").environ.get("WSCORE", "0.8")), "doa": 90, "snr_db": 20, "preroll_ms": len(preroll) // 32}))
    ws.send(b"\x01" + preroll)
    stream = cmd_pcm + silence(3000)
    for i in range(0, len(stream), 640):          # 20 ms chunks in real time
        if eot_at[0] or done.is_set():
            break
        ws.send(b"\x01" + stream[i:i + 640])
        time.sleep(0.02)
    speech_end = len(cmd_pcm) / 32000
    done.wait(60)

for ts, m in events:
    if m["type"] not in ("stream_open", "stream_close"):
        print("%6.2fs %s" % (ts, json.dumps(m)[:200]))
    else:
        print("%6.2fs %s" % (ts, m["type"]))
first_audio = next((ts for ts, m in events if m["type"] == "stream_open"), None)
print("speech ended at %.2fs" % speech_end)
if eot_at[0]:
    print("end of turn detected %.0f ms after the end of speech" % ((eot_at[0] - t0 - speech_end) * 1000))
if first_audio:
    print("first answer audio %.0f ms after the end of speech" % ((first_audio - speech_end) * 1000))
if audio:
    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(audio))
    print("answer: %.1f s of audio in %s" % (len(audio) / 48000, args.out))
