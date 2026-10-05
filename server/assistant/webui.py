"""Admin web UI of the server (Flask): dashboard, devices and pairing,
conversations with latency traces, chat and push-to-talk, timers, media,
memory, users, skills, settings and logs."""

import functools
import hmac
import json
import os
import queue
import time

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from . import __version__
from .audio import wav_bytes
from .devices import DeviceSession

STATIC = os.path.join(os.path.dirname(__file__), "static")


def create_app(app):
    web = Flask(__name__, static_folder=None)

    def auth(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            pw = app.settings["server"].get("web_password")
            if pw:
                c = request.authorization
                if not c or not hmac.compare_digest((c.password or "").encode(), pw.encode()):
                    return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="assistant"'})
            return fn(*a, **kw)
        return wrapper

    def body():
        d = request.get_json(silent=True)
        return d if isinstance(d, dict) else {}

    @web.get("/")
    @auth
    def index():
        return send_from_directory(STATIC, "index.html")

    @web.get("/static/<path:name>")
    @auth
    def static(name):
        return send_from_directory(STATIC, name)

    # ------------------------------------------------------------ overview
    @web.get("/api/overview")
    @auth
    def overview():
        timers = app.store.active_timers()
        return jsonify({
            "version": __version__, "name": app.settings["server"]["name"], "uptime": time.time() - app.started,
            "health": app.health(), "devices": app.devices.all_known(), "active": app.turns.view(),
            "media": app.media.status(), "timers": len(timers),
            "next_timer": timers[0] if timers else None,
            "turns": app.store.turns(8),
            "gateway_port": app.settings["server"]["gateway_port"],
        })

    @web.get("/api/events")
    @auth
    def events():
        q = app.bus.subscribe()

        def gen():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        msg = q.get(timeout=15)
                        yield "data: %s\n\n" % json.dumps(msg, default=str)
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                app.bus.unsubscribe(q)
        return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-store"})

    @web.post("/api/health/check")
    @auth
    def health_check():
        return jsonify(app.check_health())

    # ------------------------------------------------------------ devices
    @web.get("/api/devices/<dev>/log")
    @auth
    def device_log(dev):
        return jsonify(app.device_logs.get(dev))

    @web.post("/api/devices/<dev>/wake_templates")
    @auth
    def device_wake_templates(dev):
        (app.wakewords.hold.add if body().get("hold") else app.wakewords.hold.discard)(dev)
        return jsonify({"hold": dev in app.wakewords.hold})

    @web.get("/api/firmware")
    @auth
    def firmware_info():
        from .firmware import available
        fw = available()
        return jsonify({k: v for k, v in (fw or {}).items() if k != "path"})

    @web.post("/api/devices/<dev>/ota")
    @auth
    def device_ota(dev):
        s = app.devices.get(dev)
        if not s:
            return jsonify({"error": "the device is offline"}), 400
        return jsonify(app.ota.start(s))

    @web.get("/api/devices/<dev>/ota")
    @auth
    def device_ota_status(dev):
        job = app.ota.status(dev) or {}
        return jsonify({k: v for k, v in job.items() if k != "cond"})

    @web.get("/api/devices")
    @auth
    def devices():
        return jsonify(app.devices.all_known())

    @web.post("/api/devices/<dev>")
    @auth
    def device_update(dev):
        d = body()
        fields = {k: d[k] for k in ("name", "room", "user") if k in d}
        if isinstance(d.get("engine"), dict):
            fields["engine"] = d["engine"]
        rec = app.devices.update(dev, **fields)
        if not rec:
            abort(404)
        rec.pop("token_hash", None)
        return jsonify(rec)

    @web.post("/api/devices/<dev>/approve")
    @auth
    def device_approve(dev):
        app.devices.approve(dev, bool(body().get("approved", True)))
        return jsonify({"ok": True})

    @web.delete("/api/devices/<dev>")
    @auth
    def device_delete(dev):
        s = app.devices.get(dev)
        app.store.delete_device(dev)
        if s:
            s.close()
            try:
                s.ws.close()
            except Exception:
                pass
        return jsonify({"ok": True})

    @web.post("/api/devices/<dev>/action")
    @auth
    def device_action(dev):
        s = app.devices.get(dev)
        if not s:
            return jsonify({"error": "device offline"}), 409
        d = body()
        a = d.get("action")
        if a == "say":
            import threading
            threading.Thread(target=app.router.speak, args=(s, d.get("text", "Hello!")), daemon=True).start()
        elif a == "earcon":
            s.send({"type": "earcon", "name": d.get("name", "notify")})
        elif a == "stop":
            app.media.stop(s)
            app.notifier.stop_ringing(s.id)
            s.stop_all()
        elif a == "volume":
            s.set(volume=int(d.get("volume", 50)))
        elif a == "mute":
            s.set(mic_muted=bool(d.get("muted")))
        elif a == "led":
            s.send({"type": "led", "pattern": d.get("pattern", "success"), "on": d.get("on", True)})
        elif a == "ring":
            app.notifier.ring(s, "This is a test alarm.")
        elif a == "listen":
            s.send({"type": "listen", "timeout_ms": 6000, "earcon": True})
        else:
            return jsonify({"error": "unknown action"}), 400
        return jsonify({"ok": True})

    # ------------------------------------------------------------ turns
    @web.get("/api/turns")
    @auth
    def turns():
        before = request.args.get("before", type=float)
        return jsonify(app.store.turns(request.args.get("limit", 50, type=int), before))

    @web.get("/api/turns/<tid>/audio")
    @auth
    def turn_audio(tid):
        t = app.store.one("SELECT audio FROM turns WHERE id = ?", (tid,))
        if not t or not t["audio"] or not os.path.exists(t["audio"]):
            abort(404)
        return send_file(t["audio"], mimetype="audio/wav")

    # ------------------------------------------------------------ chat / talk
    @web.post("/api/chat")
    @auth
    def chat():
        d = body()
        text = (d.get("text") or "").strip()
        if not text:
            return jsonify({"error": "empty"}), 400
        user = d.get("user") or "household"
        chat_id = d.get("chat_id") or user
        app.messaging.web_history[chat_id].append({"from": "user", "text": text, "ts": time.time()})
        t0 = time.time()
        reply = app.messaging.handle_text("web", chat_id, user, text)
        app.messaging.web_history[chat_id].append({"from": "assistant", "text": reply, "ts": time.time()})
        return jsonify({"reply": reply, "ms": round((time.time() - t0) * 1000)})

    @web.get("/api/chat/history")
    @auth
    def chat_history():
        return jsonify(list(app.messaging.web_history[request.args.get("chat_id", "household")]))

    @web.post("/api/chat/reset")
    @auth
    def chat_reset():
        chat_id = body().get("chat_id", "household")
        app.conversations.reset("web:%s" % chat_id)
        app.messaging.web_history[chat_id].clear()
        return jsonify({"ok": True})

    @web.get("/api/browser_token")
    @auth
    def browser_token():
        host = request.host.split(":")[0]
        return jsonify({"url": "ws://%s:%d/v1/device" % (host, app.settings["server"]["gateway_port"]),
                        "token": app.browser_secret})

    @web.post("/api/tts")
    @auth
    def tts_preview():
        d = body()
        pcm = app.tts.synthesize(d.get("text", "Hello, this is my voice."), d.get("voice"))
        return Response(wav_bytes(pcm, app.tts.RATE), mimetype="audio/wav")

    @web.get("/api/voices")
    @auth
    def voices():
        return jsonify(app.tts.voices())

    # ------------------------------------------------------------ timers
    @web.get("/api/timers")
    @auth
    def timers():
        out = []
        for t in app.store.active_timers():
            dev = app.devices.info(t["device_id"]) if t["device_id"] else None
            out.append({**t, "device": (dev or {}).get("name", ""), "left": max(0, t["due"] - time.time())})
        return jsonify(out)

    @web.delete("/api/timers/<int:tid>")
    @auth
    def timer_delete(tid):
        app.store.set_timer_status(tid, "cancelled")
        app.notifier.reschedule()
        return jsonify({"ok": True})

    @web.post("/api/timers")
    @auth
    def timer_add():
        d = body()
        dev = d.get("device_id", "")
        due = time.time() + float(d.get("seconds", 60))
        tid = app.store.add_timer(d.get("kind", "timer"), due, d.get("label", ""), float(d.get("seconds", 60)), dev)
        app.notifier.reschedule()
        return jsonify({"id": tid})

    # ------------------------------------------------------------ media
    @web.get("/api/media")
    @auth
    def media():
        return jsonify({"playing": app.media.status(), "stations": app.settings["media"]["stations"]})

    @web.post("/api/media/play")
    @auth
    def media_play():
        d = body()
        st = app.media.find_station(d.get("station", ""))
        sessions = [s for s in (app.devices.get(i) for i in d.get("devices", [])) if s]
        if not st or not sessions:
            return jsonify({"error": "pick a station and at least one online device"}), 400
        app.media.play(st, sessions)
        return jsonify({"ok": True})

    @web.post("/api/media/stop")
    @auth
    def media_stop():
        for i in body().get("devices", []):
            s = app.devices.get(i)
            if s:
                app.media.stop(s)
        return jsonify({"ok": True})

    # ------------------------------------------------------------ memory / users
    @web.get("/api/memory")
    @auth
    def memory():
        return jsonify(app.store.facts())

    @web.post("/api/memory")
    @auth
    def memory_add():
        d = body()
        if not d.get("text"):
            return jsonify({"error": "empty"}), 400
        return jsonify({"id": app.store.add_fact(d["text"], d.get("user", ""))})

    @web.delete("/api/memory/<int:fid>")
    @auth
    def memory_delete(fid):
        app.store.delete_fact(fid)
        return jsonify({"ok": True})

    @web.get("/api/users")
    @auth
    def users():
        from .integrations import public_view
        users = app.store.users()
        for u in users:
            u["services"] = list(public_view(json.loads(u.pop("settings", None) or "{}")).keys())
        return jsonify({"users": users})

    @web.post("/api/users")
    @auth
    def user_add():
        d = body()
        name = (d.get("name") or "").strip().lower()
        if not name or d.get("role") not in ("admin", "adult", "child", "guest"):
            return jsonify({"error": "name and role (admin, adult, child, guest) required"}), 400
        app.store.x("INSERT INTO users (name, role, created) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET role = excluded.role", (name, d["role"], time.time()))
        return jsonify({"ok": True})

    @web.delete("/api/users/<name>")
    @auth
    def user_delete(name):
        if name == "household":
            return jsonify({"error": "the household user is built in"}), 400
        app.store.x("DELETE FROM users WHERE name = ?", (name,))
        return jsonify({"ok": True})

    # ------------------------------------------------------------ channels (integrations)
    @web.get("/api/channels")
    @auth
    def channels():
        return jsonify([ch.view() for ch in app.messaging.channels.values()])

    @web.post("/api/channels/<name>")
    @auth
    def channel_set(name):
        ch = app.messaging.channels.get(name) or abort(404)
        d = body()
        allowed = {k for k, _, t in ch.FIELDS if t != "readonly"} | {"enabled"}
        changes = {k: v for k, v in d.items() if k in allowed and v != "********"}
        ch.update_cfg(changes)
        return jsonify(ch.view())

    @web.post("/api/channels/<name>/link")
    @auth
    def channel_link(name):
        ch = app.messaging.channels.get(name) or abort(404)
        d = body()
        ch.link(str(d.get("id", "")).strip(), (d.get("user") or "").strip())
        return jsonify(ch.view())

    @web.post("/api/channels/<name>/action/<act>")
    @auth
    def channel_action(name, act):
        ch = app.messaging.channels.get(name) or abort(404)
        return jsonify(ch.api(act, body()))

    # ------------------------------------------------------------ per-person service accounts
    @web.get("/api/users/<name>/services")
    @auth
    def user_services(name):
        from .integrations import SERVICES, public_view
        return jsonify({"services": {k: {"title": v["title"], "fields": v["fields"]} for k, v in SERVICES.items()},
                        "values": public_view(app.store.user_settings(name))})

    @web.post("/api/users/<name>/services")
    @auth
    def user_services_set(name):
        from .integrations import merge, public_view
        new = merge(app.store.user_settings(name), body())
        app.store.set_user_settings(name, new)
        return jsonify(public_view(new))

    @web.post("/api/users/<name>/services/<svc>/test")
    @auth
    def user_service_test(name, svc):
        from .integrations import account
        acct = account(app, name, svc)
        if not acct or acct.get("_user") != name:
            return jsonify({"error": "fill in and save this account first"}), 400
        try:
            if svc == "jellyfin":
                from .skills.jellyfin import Jellyfin, _auth
                _auth.clear()
                jf = Jellyfin(acct)
                n = jf.get("/Items", userId=jf.user_id, Recursive="true", Limit=0).get("TotalRecordCount")
                return jsonify({"ok": True, "message": "Connected: %s items in the library" % n})
            if svc == "subsonic":
                from .skills.subsonic import _call
                _call(acct, "ping")
                n = len([a for i in _call(acct, "getArtists")["artists"].get("index", []) for a in i.get("artist", [])])
                return jsonify({"ok": True, "message": "Connected: %d artists" % n})
        except Exception as e:
            return jsonify({"error": str(e)[:200]}), 400
        return jsonify({"error": "unknown service"}), 400

    # ------------------------------------------------------------ skills / settings
    @web.get("/api/skills")
    @auth
    def skills():
        return jsonify(app.skills.skills())

    @web.post("/api/skills")
    @auth
    def skills_set():
        d = body()
        disabled = set(app.settings["skills"].get("disabled", []))
        (disabled.discard if d.get("enabled") else disabled.add)(d.get("name"))
        app.settings.update({"skills": {"disabled": sorted(disabled)}})
        return jsonify(app.skills.skills())

    @web.get("/api/settings")
    @auth
    def settings():
        return jsonify(app.settings.public())

    @web.post("/api/settings")
    @auth
    def settings_set():
        app.settings.clean_update(body())
        app.llm.resolved = None
        return jsonify(app.settings.public())

    @web.get("/api/logs")
    @auth
    def logs():
        return jsonify(list(app.logs.lines)[-int(request.args.get("n", 400)):])

    return web
