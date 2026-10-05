"""WebSocket link to the assistant server (protocol: docs/PROTOCOL.md).

Runs in its own thread with the synchronous websockets client, like the
satellite agent. The owner gets what the server sends through three callbacks,
called from the link thread:

    on_message(msg)            JSON messages (dict)
    on_audio(stream_id, pcm)   0x02 playback frames
    on_status(status, detail)  connecting | pending | online | offline | error
"""

import json
import logging
import queue
import random
import ssl
import threading

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from . import __version__

log = logging.getLogger("link")

AUDIO_UP, AUDIO_DOWN = 0x01, 0x02
BACKOFF = [1, 2, 5, 10]         # seconds, capped (PROTOCOL.md section 1)


class ServerLink:
    def __init__(self, hello_fn, on_message, on_audio, on_status):
        self.hello_fn = hello_fn        # () -> (url, hello dict); read at every connection
        self.on_message = on_message
        self.on_audio = on_audio
        self.on_status = on_status
        self.ws = None
        self.up = False
        self.status = "connecting"
        self.detail = ""
        self.stopping = False
        self.outq = queue.Queue(maxsize=2000)
        self.wake_up = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="link", daemon=True)
        self.thread.start()

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

    def reconnect(self):
        """Drops the current connection (after a settings change) and
        connects again right away."""
        if self.status == "error":
            self.status = "connecting"
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
    def _set_status(self, status, detail=""):
        self.status, self.detail = status, detail
        try:
            self.on_status(status, detail)
        except Exception:
            log.exception("status callback failed")

    def _run(self):
        failures = 0
        while not self.stopping:
            url, hello = self.hello_fn()
            try:
                kwargs = {"open_timeout": 5, "max_size": 16 * 1024 * 1024,
                          "ping_interval": 20, "ping_timeout": 20, "close_timeout": 2}
                if url.startswith("wss://"):
                    kwargs["ssl"] = ssl.create_default_context()
                if self.status != "error":      # keep showing why the server refused
                    self._set_status("connecting", url)
                with connect(url, **kwargs) as ws:
                    self.ws = ws
                    ws.send(json.dumps(hello))
                    reply = json.loads(ws.recv(timeout=10))
                    if reply.get("type") == "pending":
                        self._set_status("pending", reply.get("message", ""))
                        log.info("server: %s", reply.get("message"))
                        # stay connected: the server sends welcome once approved
                        reply = self._wait_welcome(ws)
                    if reply.get("type") == "error":
                        self._set_status("error", reply.get("message", "refused"))
                        log.warning("server refused the connection: %s", reply.get("message"))
                        failures = len(BACKOFF)     # no point retrying quickly
                        raise _Refused()
                    if reply.get("type") != "welcome":
                        raise RuntimeError("unexpected reply: %s" % str(reply)[:200])
                    failures = 0
                    self._session(ws, reply)
            except _Refused:
                pass
            except (OSError, ConnectionClosed, WebSocketException, EOFError, TimeoutError, RuntimeError,
                    ValueError) as e:
                if self.status != "error":
                    self._set_status("offline", str(e) or e.__class__.__name__)
                log.info("server link down: %s", e or e.__class__.__name__)
            except Exception as e:
                self._set_status("offline", repr(e))
                log.exception("server link failed")
            finally:
                self.ws = None
                self.up = False
            if self.stopping:
                break
            delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
            failures += 1
            self.wake_up.wait(delay + random.uniform(0, delay / 4))
            self.wake_up.clear()

    def _wait_welcome(self, ws):
        while not self.stopping:
            msg = ws.recv()
            if isinstance(msg, str):
                m = json.loads(msg)
                if m.get("type") in ("welcome", "error"):
                    return m
        raise RuntimeError("stopped")

    def _session(self, ws, welcome):
        while not self.outq.empty():
            self.outq.get_nowait()
        self.up = True
        log.info("connected to %s", welcome.get("server", "server"))
        self._set_status("online", welcome.get("server", ""))
        try:
            self.on_message(welcome)
        except Exception:
            log.exception("message callback failed")

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

        t = threading.Thread(target=sender, name="link-send", daemon=True)
        t.start()
        try:
            while not stop.is_set():
                msg = ws.recv()
                if isinstance(msg, bytes):
                    if len(msg) >= 5 and msg[0] == AUDIO_DOWN:
                        self.on_audio(int.from_bytes(msg[1:5], "little"), msg[5:])
                    continue
                m = json.loads(msg)
                if m.get("type") == "pong":
                    continue
                try:
                    self.on_message(m)
                except Exception:
                    log.exception("handling %s", m.get("type"))
        finally:
            stop.set()
            self.up = False


class _Refused(Exception):
    pass


def make_hello(cfg, tools):
    return {
        "type": "hello",
        "protocol": 1,
        "device_id": cfg["device_id"],
        "token": cfg["token"],
        "kind": "desktop",
        "name": cfg.name(),
        "room": cfg["room"],
        "owner": (cfg["owner"] or "").strip().lower(),
        "version": __version__,
        "capabilities": {
            "audio_in": {"rate": 16000, "channels": 1, "format": "s16le"},
            "audio_out": {"rates": [16000, 22050, 24000, 44100, 48000], "channels": [1, 2], "format": "s16le"},
            "confirm_ui": True,
            "display": True,
        },
        "settings": {"volume": cfg["volume"], "mic_muted": cfg["mic_muted"]},
        "tools": tools,
    }
