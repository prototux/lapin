"""Device registry and live sessions (one per connected satellite or
browser). A session owns the WebSocket send side: control messages and
audio go through separate queues so control is never stuck behind audio."""

import itertools
import json
import logging
import queue
import re
import threading
import time

log = logging.getLogger("devices")

AUDIO_DOWN = 0x02
# Messages that may overtake queued audio. Everything else (stream_open,
# stream_close, session_end, reply...) stays in order with the audio: a
# stream must not be closed before its last samples have been delivered.
URGENT = {"cancel", "eot", "stop", "pong", "wake_ack", "set", "led", "earcon", "alarm", "transcript", "listen",
          "error", "welcome", "stream_pause", "tool_call", "confirm"}


class OutStream:
    """One downlink audio stream, paced so the device never holds more than
    `lead` seconds ahead of playback (bounded memory on the satellite)."""

    def __init__(self, session, kind, rate, channels, start_at=None, lead=6.0, gain_db=0.0,
                 prebuffer_ms=None):
        self.session = session
        self.id = session.next_stream_id()
        self.kind = kind
        self.rate = rate
        self.channels = channels
        self.lead = lead
        self.bytes_per_s = rate * channels * 2
        self.start = start_at or time.time()
        self.sent = 0
        self.closed = False
        msg = {"type": "stream_open", "id": self.id, "kind": kind, "rate": rate, "channels": channels,
               "gain_db": gain_db}
        if start_at:
            msg["start_at_ns"] = int(start_at * 1e9)
        if prebuffer_ms is not None:
            msg["prebuffer_ms"] = prebuffer_ms
        session.send(msg)

    def write(self, pcm, cancel=None):
        if self.closed or not pcm:
            return
        frame = 2 * self.channels
        for i in range(0, len(pcm), 9600):
            chunk = pcm[i:i + 9600]
            chunk = chunk[:len(chunk) - len(chunk) % frame]
            ahead = self.start + self.sent / self.bytes_per_s - time.time()
            while ahead > self.lead:
                if (cancel is not None and cancel.is_set()) or self.closed or not self.session.alive:
                    return
                time.sleep(min(0.1, ahead - self.lead))
                ahead = self.start + self.sent / self.bytes_per_s - time.time()
            self.session.send_audio(self.id, chunk)
            self.sent += len(chunk)

    def pause(self):
        self.paused_at = time.time()
        self.session.send({"type": "stream_pause", "id": self.id, "paused": True})

    def resume(self):
        # what the device still holds is played after the resume
        held = max(0.0, self.start + self.sent / self.bytes_per_s - getattr(self, "paused_at", time.time()))
        self.start = time.time() + held - self.sent / self.bytes_per_s
        self.session.send({"type": "stream_pause", "id": self.id, "paused": False})

    def remaining(self):
        """Seconds until everything sent so far has been played."""
        return max(0.0, self.start + self.sent / self.bytes_per_s - time.time())

    def close(self, drain=True):
        if not self.closed:
            self.closed = True
            self.session.send({"type": "stream_close", "id": self.id, "drain": drain})


def clean_tools(tools):
    """Validates the tools a device declares in its hello."""
    out = []
    for t in (tools or [])[:40]:
        if not isinstance(t, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", str(t.get("name", ""))):
            continue
        params = t.get("parameters") if isinstance(t.get("parameters"), dict) else {}
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        out.append({"name": t["name"], "description": str(t.get("description", ""))[:500], "parameters": params,
                    "confirm": bool(t.get("confirm")), "silent": bool(t.get("silent")),
                    "summary": str(t.get("summary", ""))[:200],
                    "replaces": [str(x) for x in (t.get("replaces") or [])][:10]})
    return out


class DeviceSession:
    def __init__(self, app, ws, hello, record):
        self.app = app
        self.ws = ws
        self.id = hello["device_id"]
        self.kind = hello.get("kind", "satellite")
        self.name = record.get("name") or hello.get("name") or self.id
        self.room = record.get("room") or ""
        self.version = hello.get("version")
        self.caps = hello.get("capabilities", {})
        settings = hello.get("settings", {})
        self.volume = settings.get("volume")
        self.mic_muted = settings.get("mic_muted", False)
        self.wake_words = hello.get("wake_words", [])
        self.tools = clean_tools(hello.get("tools"))    # actions the device runs itself (phone, desktop)
        self.owner = record.get("user") or ""
        self.calls = {}             # tool call id -> [Event, result]
        self.state = "idle"
        self.connected_at = time.time()
        self.telemetry = {}
        self.alive = True
        self.ctrl = queue.Queue()
        self.audio = queue.Queue(maxsize=2000)
        self._ids = itertools.count(1)
        self.playback = {}          # stream id -> last event
        self.playback_cb = {}       # stream id -> callback(event)
        self.remote = ws.remote_address[0] if getattr(ws, "remote_address", None) else ""
        self.wake = threading.Event()
        threading.Thread(target=self._sender, name="send-%s" % self.id, daemon=True).start()

    # ------------------------------------------------------------ device tools
    def call_tool(self, name, args, timeout=20):
        """Runs one of the device's own tools; its result (a dict)."""
        cid = "c%d" % next(self._ids)
        ev = threading.Event()
        self.calls[cid] = [ev, None]
        self.send({"type": "tool_call", "call_id": cid, "name": name, "args": args or {}})
        try:
            if not ev.wait(timeout):
                return {"error": "the device did not answer in time"}
            res = self.calls[cid][1]
            return res if isinstance(res, dict) else {"result": res}
        finally:
            self.calls.pop(cid, None)

    def on_tool_result(self, m):
        c = self.calls.get(m.get("call_id"))
        if c:
            c[1] = m.get("result") if "result" in m else {"error": m.get("error") or "failed"}
            c[0].set()

    # ------------------------------------------------------------ sending
    def next_stream_id(self):
        return next(self._ids)

    def send(self, msg):
        if not self.alive:
            return
        if msg.get("type") in URGENT:
            self.ctrl.put(json.dumps(msg))
        else:
            self.audio.put(json.dumps(msg))
        self.wake.set()

    def send_binary(self, data):
        """A raw binary frame, in order with the audio queue (firmware updates)."""
        if self.alive:
            self.audio.put(bytes(data), timeout=30)
            self.wake.set()

    def send_audio(self, stream_id, pcm):
        if not self.alive:
            return
        try:
            self.audio.put(bytes([AUDIO_DOWN]) + stream_id.to_bytes(4, "little") + pcm, timeout=5)
            self.wake.set()
        except queue.Full:
            log.warning("%s: audio queue full", self.name)

    def _sender(self):
        while self.alive:
            self.wake.wait(0.5)
            self.wake.clear()
            try:
                while True:
                    try:
                        item = self.ctrl.get_nowait()
                    except queue.Empty:
                        try:
                            item = self.audio.get_nowait()
                        except queue.Empty:
                            break
                    self.ws.send(item)
                    if not self.ctrl.empty():
                        continue
            except Exception as e:
                log.info("%s: send failed: %s", self.name, e)
                self.alive = False
                try:
                    self.ws.close()
                except Exception:
                    pass

    # ------------------------------------------------------------ helpers
    def open_stream(self, kind="tts", rate=24000, channels=1, start_at=None, lead=None, gain_db=0.0,
                    prebuffer_ms=None):
        if lead is None:
            lead = 1.5 if kind == "media" else 6.0
        return OutStream(self, kind, rate, channels, start_at, lead, gain_db, prebuffer_ms)

    def set(self, **settings):
        if "volume" in settings:
            self.volume = settings["volume"]
        self.send({"type": "set", **settings})

    def stop_all(self, media=True):
        self.send({"type": "stop", "media": media})

    def on_playback(self, msg):
        sid = msg.get("id")
        if (msg.get("event") or msg.get("what")) in ("underrun", "overflow"):
            log.warning("%s: playback %s on stream %s (%s)", self.name, msg.get("event") or msg.get("what"), sid,
                        msg.get("kind", ""))
        self.playback[sid] = msg.get("event") or msg.get("what")
        cb = self.playback_cb.get(sid)
        if cb:
            try:
                cb(msg)
            except Exception:
                log.exception("playback callback")
        if msg.get("what") in ("finished", "stopped"):
            self.playback_cb.pop(sid, None)

    def view(self):
        return {"id": self.id, "name": self.name, "room": self.room, "kind": self.kind, "state": self.state,
                "volume": self.volume, "mic_muted": self.mic_muted, "version": self.version,
                "wake_words": self.wake_words, "connected_at": self.connected_at, "remote": self.remote,
                "telemetry": self.telemetry, "online": self.alive}

    def close(self):
        self.alive = False
        self.wake.set()


class DeviceManager:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.sessions = {}
        self.approvals = {}         # device id -> Event, while a pending device waits

    def attach(self, s):
        with self.lock:
            old = self.sessions.get(s.id)
            self.sessions[s.id] = s
        if old:
            old.close()
        self.app.bus.publish("device", device=s.view(), event="online")

    def detach(self, s):
        with self.lock:
            if self.sessions.get(s.id) is s:
                del self.sessions[s.id]
        s.close()
        self.app.store.update_device(s.id, last_seen=time.time())
        self.app.bus.publish("device", device=s.view(), event="offline")

    def get(self, device_id):
        with self.lock:
            return self.sessions.get(device_id)

    def online(self, kind=None):
        with self.lock:
            return [s for s in self.sessions.values() if s.alive and (kind is None or s.kind == kind)]

    def info(self, device_id):
        s = self.get(device_id)
        if s:
            return {"name": s.name, "room": s.room}
        d = self.app.store.device(device_id)
        return {"name": d["name"], "room": d["room"]} if d else None

    def all_known(self):
        out = []
        live = {s.id: s for s in self.online()}
        for d in self.app.store.devices():
            s = live.pop(d["id"], None)
            d["online"] = bool(s)
            if s:
                d.update(state=s.state, volume=s.volume, mic_muted=s.mic_muted, telemetry=s.telemetry,
                         version=s.version, remote=s.remote, wake_words=s.wake_words,
                         tools=[t["name"] for t in s.tools])
            out.append(d)
        for s in live.values():      # browser endpoints are not stored
            out.append({**s.view(), "status": "approved"})
        return out

    def resolve(self, target, ctx):
        """'here', 'all', a room or a device name -> online sessions."""
        sessions = [s for s in self.online() if s.kind == "satellite" or s.id == ctx.device_id]
        t = (target or "here").strip().lower()
        if t in ("here", "this", "this room", "", "me"):
            s = self.get(ctx.device_id)
            if s:
                return [s]
            sats = [x for x in sessions if x.kind == "satellite"]
            return sats[:1] if len(sats) == 1 else []
        if t in ("all", "everywhere", "every room", "all rooms", "house", "whole house", "home"):
            return sessions
        t = t.replace("the ", "")
        by_room = [s for s in sessions if s.room and s.room.lower() in t or t in (s.room or "").lower()]
        if by_room:
            return by_room
        return [s for s in sessions if t in s.name.lower() or s.name.lower() in t]

    def approve(self, device_id, approved=True):
        self.app.store.update_device(device_id, status="approved" if approved else "blocked")
        ev = self.approvals.get(device_id)
        if ev:
            ev.set()

    def update(self, device_id, **fields):
        """Name / room / engine settings from the admin UI, pushed live."""
        rec = self.app.store.device(device_id)
        if not rec:
            return None
        changes = {k: v for k, v in fields.items() if k in ("name", "room", "user")}
        cfg = rec["config"]
        if "engine" in fields:
            cfg.setdefault("engine", {}).update(fields["engine"])
            changes["config"] = cfg
        if changes:
            self.app.store.update_device(device_id, **changes)
        s = self.get(device_id)
        if s:
            s.name = fields.get("name", s.name)
            s.room = fields.get("room", s.room)
            msg = {"type": "config", **{k: fields[k] for k in ("name", "room") if k in fields}}
            if "engine" in fields:
                msg["engine"] = fields["engine"]
            s.send(msg)
        return self.app.store.device(device_id)
