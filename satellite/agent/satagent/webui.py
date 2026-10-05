"""Local web UI of the satellite (Flask): status, audio settings, wake word
enrollment, plugin panels, device settings and logs."""

import functools
import hmac
import json
import logging
import os
import queue
import secrets
import time

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from . import __version__, system

log = logging.getLogger("webui")
STATIC = os.path.join(os.path.dirname(__file__), "static")


def create_app(agent):
    app = Flask(__name__, static_folder=None)
    app.config["JSON_SORT_KEYS"] = False
    cfg = agent.cfg

    def auth(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            pw = cfg["webui"].get("password")
            if pw:
                a_ = request.authorization
                if not a_ or not hmac.compare_digest((a_.password or "").encode(), pw.encode()):
                    return Response("Authentication required", 401,
                                    {"WWW-Authenticate": 'Basic realm="satellite"'})
            return fn(*a, **kw)
        return wrapper

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ pages
    @app.get("/")
    @auth
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    @auth
    def static_file(name):
        return send_from_directory(STATIC, name)

    @app.get("/plugins/<name>/static/<path:fname>")
    @auth
    def plugin_static(name, fname):
        p = agent.plugins.get(name)
        if not p or not p.static_dir:
            abort(404)
        return send_from_directory(p.static_dir, fname)

    # ------------------------------------------------------------ status
    @app.get("/api/status")
    @auth
    def status():
        link = agent.link
        es = agent.engine_status
        return jsonify({
            "version": __version__,
            "device": {k: cfg[k] for k in ("device_id", "name", "room", "server_url")},
            "state": agent.state, "muted": agent.muted, "alarm": agent.alarm_on,
            "link": {"status": link.status if link else "starting", "detail": link.detail if link else "",
                     "url": link.url if link else None,
                     "up": bool(link and link.up), "rtt_ms": link.rtt_ms if link else None,
                     "clock_offset_ms": round(link.offset_ns / 1e6, 1) if link else None},
            "engine": {"connected": agent.engine.connected, "version": es.get("version"),
                       "config": es.get("config", {}), "aec_tail_ms": es.get("aec_tail_ms")},
            "settings": cfg.engine(),
            "wakeword": cfg["wakeword"],
            "keywords": agent.keywords,
            "plugins": [{"name": n, "title": p.title, "description": p.description,
                         "panel": bool(p.static_dir and os.path.exists(os.path.join(p.static_dir, "panel.js"))),
                         "status": p.status()} for n, p in agent.plugins.items()],
            "available_plugins": __import__("satagent.plugins", fromlist=["discover"]).discover(),
            "counters": agent.counters,
            "system": system.snapshot(),
            "button": {"device": agent.button.device if agent.button else None,
                       "error": agent.button.error if agent.button else "disabled"},
            "transcript": agent.last_transcript, "reply": agent.last_reply,
        })

    @app.get("/api/meters")
    @auth
    def meters():
        return jsonify(agent.meters)

    @app.get("/api/events")
    @auth
    def events():
        q = queue.Queue(maxsize=200)
        agent.listeners.add(q)

        def gen():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        msg = q.get(timeout=15)
                        yield "data: %s\n\n" % json.dumps(msg)
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                agent.listeners.discard(q)
        return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ settings
    @app.post("/api/engine")
    @auth
    def engine_settings():
        allowed = set(cfg.engine().keys())
        changes = {k: v for k, v in body().items() if k in allowed}
        agent.apply_engine(changes)
        return jsonify(cfg.engine())

    @app.post("/api/device")
    @auth
    def device_settings():
        data = body()
        changes = {k: str(data[k]).strip() for k in ("name", "room", "server_url") if k in data}
        if "server_url" in changes and not all(u.strip().startswith(("ws://", "wss://"))
                                                for u in changes["server_url"].split(",")):
            return jsonify({"error": "server URLs must start with ws:// or wss:// (several: comma separated)"}), 400
        if "password" in data:
            cfg.update({"webui": {"password": str(data["password"])}})
        if "button_enabled" in data:
            cfg.update({"button": {"enabled": bool(data["button_enabled"])}})
        if changes:
            cfg.update(changes)
            agent.link.reconnect()
        return jsonify({k: cfg[k] for k in ("device_id", "name", "room", "server_url")})

    @app.post("/api/wakeword_settings")
    @auth
    def wakeword_settings():
        data = body()
        if "auto_threshold" in data:
            cfg.update({"wakeword": {"auto_threshold": bool(data["auto_threshold"])}})
            if data["auto_threshold"]:
                agent.reload_wakewords()
        return jsonify(cfg["wakeword"])

    @app.post("/api/action")
    @auth
    def action():
        data = body()
        a = data.get("action")
        e = agent.engine
        if a == "wake":
            e.send("wake", source="webui")
        elif a == "stop":
            e.send("stop", media=True)
            agent._alarm(False)
        elif a == "cancel":
            e.send("cancel", reason="webui")
        elif a == "reset_noise":
            e.send("reset_noise")
        elif a == "earcon":
            e.send("earcon", name=data.get("name", "notify"))
        elif a == "reconnect":
            agent.link.reconnect()
        elif a == "new_token":
            cfg.update({"token": secrets.token_urlsafe(24)})
            agent.link.reconnect()
        elif a in ("restart_engine", "restart_agent"):
            unit = "satellite-engine" if a == "restart_engine" else "satellite-agent"
            r = system.restart(unit)
            if r.returncode:
                return jsonify({"error": r.stderr.strip() or "restart failed"}), 500
        elif a == "plugin_enable":
            name = data.get("name", "")
            cfg.update({"plugins": {name: {"enabled": bool(data.get("enabled"))}}})
            return jsonify({"ok": True, "restart_needed": True})
        else:
            return jsonify({"error": "unknown action"}), 400
        return jsonify({"ok": True})

    @app.get("/api/logs")
    @auth
    def logs():
        unit = request.args.get("unit", "satellite-agent")
        if unit not in ("satellite-agent", "satellite-engine"):
            abort(400)
        return Response(system.journal(unit, int(request.args.get("lines", 200))), mimetype="text/plain")

    # ------------------------------------------------------------ wake words
    @app.get("/api/wakewords")
    @auth
    def wakewords():
        return jsonify({"words": agent.wakeword_samples(), "loaded": agent.keywords,
                        "threshold": cfg.engine().get("kws_threshold"), "settings": cfg["wakeword"]})

    @app.post("/api/wakewords/<kw>/record")
    @auth
    def wakeword_record(kw):
        try:
            name, report = agent.record_sample(kw, int(body().get("ms", 2500)))
        except (ValueError, RuntimeError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"sample": name, "report": report})

    @app.post("/api/wakewords/<kw>/clear_negatives")
    @auth
    def wakeword_clear_neg(kw):
        import shutil
        shutil.rmtree(os.path.join(cfg.wakeword_dir, agent.safe_name(kw), "negatives"), ignore_errors=True)
        agent.reload_wakewords()
        return jsonify({"ok": True})

    @app.delete("/api/wakewords/<kw>")
    @app.delete("/api/wakewords/<kw>/<sample>")
    @auth
    def wakeword_delete(kw, sample=None):
        agent.delete_sample(kw, sample)
        return jsonify({"ok": True})

    @app.get("/api/wakewords/<kw>/<sample>")
    @auth
    def wakeword_file(kw, sample):
        d = os.path.join(cfg.wakeword_dir, agent.safe_name(kw))
        return send_from_directory(d, os.path.basename(sample), mimetype="audio/wav")

    # ------------------------------------------------------------ plugins
    @app.get("/api/plugins/<name>/settings")
    @auth
    def plugin_settings(name):
        p = agent.plugins.get(name) or abort(404)
        return jsonify(p.get_settings())

    @app.post("/api/plugins/<name>/settings")
    @auth
    def plugin_set(name):
        p = agent.plugins.get(name) or abort(404)
        new = p.set_settings(body())
        cfg.update({"plugins": {name: new}})
        return jsonify(new)

    @app.route("/api/plugins/<name>/api/<act>", methods=["GET", "POST"])
    @auth
    def plugin_api(name, act):
        p = agent.plugins.get(name) or abort(404)
        return jsonify(p.api(act, body() if request.method == "POST" else dict(request.args)))

    # ------------------------------------------------------------ live audio
    @app.get("/api/monitor")
    @auth
    def monitor():
        """The processed beam (16 kHz s16le mono), for listening in the browser."""
        q = queue.Queue(maxsize=100)
        agent.monitor_listeners.add(q)
        agent.engine.subscribe(monitor=True)

        def gen():
            try:
                deadline = time.time() + 600
                while time.time() < deadline:
                    try:
                        yield q.get(timeout=5)
                    except queue.Empty:
                        break
            finally:
                agent.monitor_listeners.discard(q)
                if not agent.monitor_listeners:
                    agent.engine.subscribe(monitor=False)
        return Response(gen(), mimetype="application/octet-stream", headers={"Cache-Control": "no-store"})

    return app
