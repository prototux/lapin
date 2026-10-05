#!/usr/bin/env python3
"""WebSocket bridge for host tests: connects to the assistant server and
relays frames to/from a child process (hostsat) over its stdin/stdout.

Frame format on the pipes: 1 byte type ('C' connected, 'D' disconnected,
'T' text, 'B' binary) + u32 little-endian length + payload.

  ws_bridge.py [--dump DIR] URL -- ./hostsat [args...]

--dump DIR also saves every wake_template recording the server sends as a
WAV file (for the offline KWS tests).
"""

import argparse
import asyncio
import base64
import json
import os
import struct
import sys

import websockets


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("url")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if a.dump:
        os.makedirs(a.dump, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE)
    ws = await websockets.connect(a.url, max_size=16 * 1024 * 1024)

    def to_child(t, payload):
        proc.stdin.write(t + struct.pack("<I", len(payload)) + payload)

    to_child(b"C", b"")
    await proc.stdin.drain()
    stats = {"rx_text": 0, "rx_bin": 0, "tx_text": 0, "tx_bin": 0}

    async def ws_to_child():
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    stats["rx_bin"] += 1
                    to_child(b"B", msg)
                else:
                    stats["rx_text"] += 1
                    if a.dump and '"wake_template"' in msg:
                        m = json.loads(msg)
                        with open(os.path.join(a.dump, m["name"]), "wb") as f:
                            f.write(base64.b64decode(m["wav_b64"]))
                    to_child(b"T", msg.encode())
                await proc.stdin.drain()
        except websockets.ConnectionClosed:
            pass
        try:
            to_child(b"D", b"")
            await proc.stdin.drain()
        except Exception:
            pass

    async def child_to_ws():
        r = proc.stdout
        while True:
            try:
                t = await r.readexactly(1)
                (n,) = struct.unpack("<I", await r.readexactly(4))
                payload = await r.readexactly(n)
            except asyncio.IncompleteReadError:
                break
            if t == b"T":
                stats["tx_text"] += 1
                await ws.send(payload.decode())
            elif t == b"B":
                stats["tx_bin"] += 1
                await ws.send(payload)
        await ws.close()

    tasks = [asyncio.create_task(ws_to_child()), asyncio.create_task(child_to_ws())]
    try:
        await asyncio.wait_for(proc.wait(), a.timeout)
    except asyncio.TimeoutError:
        proc.kill()
    for t in tasks:
        t.cancel()
    await ws.close()
    print("bridge: %s, child exit %s" % (stats, proc.returncode), file=sys.stderr)
    sys.exit(proc.returncode or 0)


asyncio.run(main())
