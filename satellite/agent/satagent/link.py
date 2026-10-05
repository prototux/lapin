"""WebSocket link to the assistant server (protocol: docs/PROTOCOL.md)."""

import json
import logging
import queue
import random
import ssl
import threading
import time

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from . import __version__

log = logging.getLogger("link")

AUDIO_UP, AUDIO_DOWN = 0x01, 0x02


class ServerLink:
    """Keeps the session with the server alive; messages go both ways.

    agent.on_server(msg) and agent.on_server_audio(stream_id, pcm) get what the
    server sends; agent.on_link(up, info) reports connection changes.
    """

    def __init__(self, agent):
        self.agent = agent
        self.ws = None
        self.up = False
        self.status = "connecting"
        self.detail = ""
        self.stopping = False
        self.outq = queue.Queue(maxsize=4000)
        self.offset_ns = 0              # server clock - local clock
        self.url = None
        self.rtt_ms = None
        self._samples = []
        self.wake_up = threading.Event()
        threading.Thread(target=self._run, name="link", daemon=True).start()

    # ------------------------------------------------------------ public
    def send(self, msg):
        if not self.up:
            return False
        try:
            self.outq.put_nowait(json.dumps(msg))
            return True
        except queue.Full:
            return False

    def send_audio(self, pcm):
        if not self.up:
            return False
        try:
            self.outq.put_nowait(bytes([AUDIO_UP]) + pcm)
            return True
        except queue.Full:
            log.warning("uplink queue full, dropping audio")
            return False

    def server_to_local_ns(self, server_ns):
        return int(server_ns) - self.offset_ns

    def reconnect(self):
        """Drops the current connection (e.g. after a settings change)."""
        ws = self.ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        self.wake_up.set()

    def stop(self):
        self.stopping = True
        self.reconnect()

    # ------------------------------------------------------------ internals
    def _hello(self):
        cfg = self.agent.cfg
        return {
            "type": "hello",
            "protocol": 1,
            "device_id": cfg["device_id"],
            "token": cfg["token"],
            "name": cfg["name"],
            "room": cfg["room"],
            "kind": "satellite",
            "version": __version__,
            "engine_version": self.agent.engine_version,
            "capabilities": {
                "audio_in": {"rate": 16000, "channels": 1, "format": "s16le"},
                "audio_out": {"rates": [16000, 24000, 48000], "channels": [1, 2], "format": "s16le"},
                "wakeword": True, "doa": True, "leds": self.agent.has_plugin("leds"),
                "button": True, "sync_playback": True,
            },
            "wake_words": self.agent.wake_words(),
            "settings": {k: v for k, v in cfg.engine().items()
                         if k in ("volume", "mic_muted", "speaker_muted")},
        }

    def _run(self):
        delay = 1.0
        attempt = 0
        while not self.stopping:
            # several URLs (comma separated) are tried in turn: e.g. a direct
            # address first, then a fallback
            urls = [u.strip() for u in self.agent.cfg["server_url"].split(",") if u.strip()]
            url = urls[attempt % len(urls)]
            attempt += 1
            try:
                kwargs = {"open_timeout": 4, "max_size": 16 * 1024 * 1024,
                          "ping_interval": 15, "ping_timeout": 15, "close_timeout": 2}
                if url.startswith("wss://"):
                    kwargs["ssl"] = ssl.create_default_context()
                self.status, self.detail = "connecting", url
                with connect(url, **kwargs) as ws:
                    self.ws = ws
                    ws.send(json.dumps(self._hello()))
                    reply = json.loads(ws.recv(timeout=10))
                    if reply.get("type") == "pending":
                        self.status = "pending"
                        self.detail = reply.get("message", "waiting for approval on the server")
                        self.agent.on_link(False, {"status": "pending"})
                        log.info("server: %s", self.detail)
                        # stay connected: the server sends welcome once approved
                        reply = self._wait_welcome(ws)
                    if reply.get("type") != "welcome":
                        raise RuntimeError(reply.get("message") or reply.get("reason") or str(reply))
                    delay = 1.0
                    attempt -= 1            # stick to the URL that works
                    self.url = url
                    self._session(ws, reply)
            except (OSError, ConnectionClosed, WebSocketException, EOFError, TimeoutError, RuntimeError,
                    ValueError) as e:
                self.detail = str(e) or e.__class__.__name__
                if self.status != "pending":
                    self.status = "offline"
                log.info("server link down: %s", self.detail)
            except Exception as e:
                self.status, self.detail = "offline", repr(e)
                log.exception("server link failed")
            finally:
                self.ws = None
                if self.up:
                    self.up = False
                    self.agent.on_link(False, {"status": self.status})
            if self.stopping:
                break
            # next URL right away; backoff once all were tried
            if attempt % len(urls):
                continue
            self.wake_up.wait(delay + random.uniform(0, delay / 2))
            self.wake_up.clear()
            delay = min(delay * 2, 30)

    def _wait_welcome(self, ws):
        while not self.stopping:
            msg = ws.recv()
            if isinstance(msg, str):
                m = json.loads(msg)
                if m.get("type") in ("welcome", "error"):
                    return m
        raise RuntimeError("stopped")

    def _session(self, ws, welcome):
        self.status, self.detail = "online", welcome.get("server", "")
        self._samples = []
        self.offset_ns = int(welcome.get("server_time_ns", time.time_ns())) - time.time_ns()
        while not self.outq.empty():
            self.outq.get_nowait()
        self.up = True
        log.info("connected to server %s", self.url)
        self.agent.on_link(True, welcome)

        stop = threading.Event()

        def sender():
            while not stop.is_set():
                try:
                    item = self.outq.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    ws.send(item)
                except Exception:
                    stop.set()
                    break

        def pinger():
            n = 0
            while not stop.wait(2 if n < 5 else 20):
                n += 1
                self.send({"type": "ping", "t0": time.time_ns()})

        threads = [threading.Thread(target=sender, daemon=True), threading.Thread(target=pinger, daemon=True)]
        for t in threads:
            t.start()
        try:
            while not stop.is_set():
                msg = ws.recv()
                if isinstance(msg, bytes):
                    if msg and msg[0] == AUDIO_DOWN and len(msg) >= 5:
                        self.agent.on_server_audio(int.from_bytes(msg[1:5], "little"), msg[5:])
                    continue
                m = json.loads(msg)
                if m.get("type") == "pong":
                    self._clock(m)
                else:
                    self.agent.on_server(m)
        finally:
            stop.set()

    def _clock(self, m):
        t2 = time.time_ns()
        t0, t1 = int(m["t0"]), int(m["t1"])
        rtt = t2 - t0
        self._samples = (self._samples + [(rtt, t1 - (t0 + t2) // 2)])[-8:]
        best = min(self._samples)          # lowest round trip = best estimate
        self.rtt_ms = best[0] / 1e6
        self.offset_ns = best[1]
