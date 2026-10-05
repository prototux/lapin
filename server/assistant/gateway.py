"""Device gateway: WebSocket endpoint for satellites and browser clients
(protocol: docs/PROTOCOL.md). Authentication: each device has an id and a
token; a new device waits until it is approved in the admin UI (or
automatically with server.auto_approve). Browser clients use a short-lived
secret handed out by the (password-protected) admin UI."""

import base64
import json
import logging
import threading
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from . import __version__
from .devices import DeviceSession
from .store import token_hash

log = logging.getLogger("gateway")
AUDIO_UP = 0x01


class Gateway:
    def __init__(self, app):
        self.app = app
        self.server = None

    def start(self):
        s = self.app.settings["server"]
        self.server = serve(self.handler, s["gateway_host"], s["gateway_port"], max_size=16 * 1024 * 1024,
                            ping_interval=20, ping_timeout=20, close_timeout=2)
        threading.Thread(target=self.server.serve_forever, name="gateway", daemon=True).start()
        log.info("device gateway on ws://%s:%d/v1/device", s["gateway_host"], s["gateway_port"])

    def stop(self):
        if self.server:
            self.server.shutdown()

    # ------------------------------------------------------------ connection
    def handler(self, ws):
        app = self.app
        try:
            hello = json.loads(ws.recv(timeout=10))
        except (TimeoutError, ValueError, ConnectionClosed):
            return
        dev_id, token = str(hello.get("device_id", ""))[:64], str(hello.get("token", ""))
        if hello.get("type") != "hello" or not dev_id or not token:
            ws.send(json.dumps({"type": "error", "message": "expected hello with device_id and token"}))
            return
        kind = hello.get("kind", "satellite")
        remote = ws.remote_address[0] if ws.remote_address else "?"
        if kind == "browser":
            if token != app.browser_secret:
                ws.send(json.dumps({"type": "error", "message": "unauthorized"}))
                return
            rec = {"name": hello.get("name") or "Browser", "room": hello.get("room", ""), "config": {}}
        else:
            rec = app.store.device(dev_id)
            info = {"version": hello.get("version"), "engine_version": hello.get("engine_version"),
                    "capabilities": hello.get("capabilities", {}), "ip": remote}
            if not rec:
                status = "approved" if app.settings["server"].get("auto_approve") else "pending"
                app.store.register_device(dev_id, token, hello.get("name") or dev_id, hello.get("room", ""),
                                          kind, info, status)
                log.info("new device %s (%s) from %s: %s", dev_id, hello.get("name"), remote, status)
                owner = str(hello.get("owner") or "").strip().lower()
                if owner and any(u["name"] == owner for u in app.store.users()):
                    # a personal device (phone, computer): its owner's, for good
                    app.store.update_device(dev_id, user=owner)
                rec = app.store.device(dev_id)
                app.bus.publish("device", event="registered", device={"id": dev_id, "name": rec["name"],
                                                                     "status": status})
            elif rec["token_hash"] != token_hash(token):
                log.warning("device %s from %s: wrong token", dev_id, remote)
                ws.send(json.dumps({"type": "error", "message": "unauthorized: unknown token for this device "
                                    "(remove it on the server to pair again)"}))
                return
            if rec["status"] == "blocked":
                ws.send(json.dumps({"type": "error", "message": "this device is blocked on the server"}))
                return
            if rec["status"] == "pending":
                ws.send(json.dumps({"type": "pending", "message": "waiting for approval in the server's "
                                    "web UI (device %s)" % dev_id}))
                ev = app.devices.approvals.setdefault(dev_id, threading.Event())
                while True:
                    ev.wait(2)
                    rec = app.store.device(dev_id)
                    if not rec or rec["status"] == "blocked":
                        ws.send(json.dumps({"type": "error", "message": "rejected by the server"}))
                        return
                    if rec["status"] == "approved":
                        break
                    try:        # still connected?
                        ws.ping().wait(5)
                    except Exception:
                        return
                app.devices.approvals.pop(dev_id, None)
            app.store.update_device(dev_id, last_seen=time.time(), info=info)

        session = DeviceSession(app, ws, hello, rec)
        app.devices.attach(session)
        cfg = dict(rec.get("config") or {})
        session.send({"type": "welcome", "server": app.settings["server"]["name"], "version": __version__,
                      "server_time_ns": time.time_ns(),
                      "config": {**cfg, "name": session.name, "room": session.room}})
        log.info("%s connected (%s, %s)", session.name, kind, remote)
        try:
            for msg in ws:
                if isinstance(msg, bytes):
                    if msg and msg[0] == AUDIO_UP:
                        app.turns.on_audio(session, msg[1:])
                    continue
                try:
                    m = json.loads(msg)
                except ValueError:
                    continue
                try:
                    self.dispatch(session, m)
                except Exception:
                    log.exception("handling %s from %s", m.get("type"), session.name)
        except ConnectionClosed:
            pass
        finally:
            app.turns.cancel_device(session, "disconnected")
            app.media.stop(session) if app.media.by_device.get(session.id) else None
            app.devices.detach(session)
            log.info("%s disconnected", session.name)

    def dispatch(self, s, m):
        app = self.app
        t = m.get("type")
        if t == "ping":
            s.send({"type": "pong", "t0": m.get("t0"), "t1": time.time_ns()})
        elif t == "wake":
            app.turns.on_wake(s, m)
        elif t == "eos":
            app.turns.on_eos(s, m)
        elif t == "audio_end":
            app.turns.on_audio_end(s, m)
        elif t == "state":
            s.state = m.get("state", s.state)
            s.mic_muted = bool(m.get("muted", s.mic_muted))
            app.bus.publish("device_state", device=s.id, name=s.name, state=s.state, muted=s.mic_muted)
        elif t == "playback":
            s.on_playback(m)
        elif t == "settings":
            if "volume" in m:
                s.volume = m["volume"]
            if "mic_muted" in m:
                s.mic_muted = bool(m["mic_muted"])
            app.bus.publish("device_state", device=s.id, name=s.name, state=s.state, muted=s.mic_muted,
                            volume=s.volume)
        elif t == "button":
            if m.get("action") == "stop":
                app.notifier.stop_ringing(s.id)
                app.media.stop(s)
            elif m.get("action") == "cancel":
                app.turns.cancel_device(s, "button")
        elif t == "wake_words":
            s.wake_words = list(m.get("wake_words", []))[:8]
            app.bus.publish("device_state", device=s.id, name=s.name, wake_words=s.wake_words)
        elif t == "wake_sample":
            try:
                wav = base64.b64decode(m.get("wav_b64", ""))
            except ValueError:
                return
            kw = m.get("keyword", "")
            new = app.wakewords.store(kw, wav)
            if new and not m.get("sync"):      # a new enrollment: learn how the recognizer hears it
                threading.Thread(target=app.verifier.learn, args=(kw, wav), daemon=True).start()
        elif t == "wake_templates_get":
            # devices with no enrollment of their own (ESP32 satellites) use the
            # recordings made on the others: one wake word everywhere
            # one message per recording (~85 KB each): small devices can't hold
            # them all in one frame
            # held back for a device (e.g. while it gets a firmware update)
            items = [] if s.id in app.wakewords.hold else app.wakewords.all(m.get("keyword"))
            for i, t in enumerate(items):
                s.send(dict(t, type="wake_template", index=i, count=len(items)))
            s.send({"type": "wake_templates_done", "count": len(items)})
        elif t == "log":
            app.device_logs.add(s, m)
        elif t == "ota_status":
            app.ota.on_status(s, m)
        elif t == "tool_result":
            s.on_tool_result(m)
        elif t == "tools":
            from .devices import clean_tools
            s.tools = clean_tools(m.get("tools"))
        elif t == "text":
            app.turns.on_text(s, str(m.get("text", ""))[:2000], speak=bool(m.get("speak", True)))
        elif t == "confirm_reply":
            app.turns.on_confirm(s, bool(m.get("yes")))
        elif t == "telemetry":
            s.telemetry = {k: m.get(k) for k in ("system", "counters", "engine", "rtt_ms")}
            s.telemetry["ts"] = time.time()
            app.bus.publish("telemetry", device=s.id, name=s.name, telemetry=s.telemetry)
