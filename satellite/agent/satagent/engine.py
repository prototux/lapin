"""Client of the satd control socket (framing in satellite/engine/src/ipc.h)."""

import json
import logging
import socket
import struct
import threading
import time

log = logging.getLogger("engine")

MSG_JSON, MSG_UPLINK, MSG_DOWNLINK, MSG_MONITOR = 1, 2, 3, 4


class EngineClient:
    """Keeps a connection to satd, reconnecting as needed.

    on_event(dict) is called for every JSON event, on_audio(bytes) for uplink
    audio and on_monitor(bytes) for the monitor stream, all from the reader
    thread. on_connect() runs after each (re)connection.
    """

    def __init__(self, path, on_event, on_audio=None, on_monitor=None, on_connect=None):
        self.path = path
        self.on_event = on_event
        self.on_audio = on_audio
        self.on_monitor = on_monitor
        self.on_connect = on_connect
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False
        self.stopping = False
        self.subscription = {"audio": True, "meters": True}
        self.thread = threading.Thread(target=self._run, name="engine", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopping = True
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------ sending
    def _send(self, typ, payload):
        frame = struct.pack("<IB", len(payload) + 1, typ) + payload
        with self.lock:
            if not self.sock:
                return False
            try:
                self.sock.sendall(frame)
                return True
            except OSError as e:
                log.warning("send failed: %s", e)
                return False

    def send(self, cmd, **fields):
        fields["cmd"] = cmd
        return self._send(MSG_JSON, json.dumps(fields).encode())

    def downlink(self, stream_id, pcm):
        return self._send(MSG_DOWNLINK, struct.pack("<I", stream_id) + pcm)

    def subscribe(self, **subs):
        self.subscription.update(subs)
        self.send("subscribe", **self.subscription)

    # ------------------------------------------------------------ reading
    def _run(self):
        delay = 0.5
        while not self.stopping:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.path)
            except OSError as e:
                if delay >= 4:
                    log.info("engine not reachable at %s (%s), retrying", self.path, e)
                time.sleep(delay)
                delay = min(delay * 2, 5)
                continue
            delay = 0.5
            with self.lock:
                self.sock = s
            self.connected = True
            log.info("connected to engine")
            self.send("subscribe", **self.subscription)
            if self.on_connect:
                try:
                    self.on_connect()
                except Exception:
                    log.exception("on_connect failed")
            try:
                self._read(s)
            except OSError as e:
                log.warning("engine connection lost: %s", e)
            with self.lock:
                self.sock = None
            self.connected = False
            try:
                s.close()
            except OSError:
                pass
            if not self.stopping:
                self.on_event({"event": "engine_down"})
                time.sleep(0.5)

    def _read(self, s):
        buf = bytearray()
        while not self.stopping:
            data = s.recv(65536)
            if not data:
                raise OSError("closed by engine")
            buf += data
            while len(buf) >= 5:
                length, typ = struct.unpack_from("<IB", buf)
                if len(buf) < 4 + length:
                    break
                payload = bytes(buf[5:4 + length])
                del buf[:4 + length]
                try:
                    if typ == MSG_JSON:
                        self.on_event(json.loads(payload))
                    elif typ == MSG_UPLINK and self.on_audio:
                        self.on_audio(payload)
                    elif typ == MSG_MONITOR and self.on_monitor:
                        self.on_monitor(payload)
                except Exception:
                    log.exception("event handler failed")
