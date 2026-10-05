"""The satellite agent: relays between the audio engine and the server."""

import base64
import logging
import os
import queue
import re
import threading
import time

from . import plugins, system
from .button import Button
from .engine import EngineClient
from .link import ServerLink

log = logging.getLogger("agent")

# engine events forwarded to the server as-is (renamed)
FORWARD = {"eos": "eos", "uplink_end": "audio_end", "state": "state"}


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine_version = None
        self.engine_status = {}
        self.keywords = []
        self.state, self.muted = "idle", bool(cfg.engine().get("mic_muted"))
        self.alarm_on = False
        self.streaming_wake = None
        self.meters = {}
        self.last_transcript, self.last_reply = "", ""
        self.counters = {"wakes": 0, "rejected": 0, "turns": 0}
        self.listeners = set()          # web UI event queues
        self.monitor_listeners = set()
        self.records = {}               # path -> threading.Event / result
        self.lock = threading.Lock()

        self.engine = EngineClient(cfg["engine_socket"], self.on_engine, self.on_uplink,
                                   self.on_monitor, self.on_engine_connect)
        self.plugins = {}
        self.link = None
        self.button = None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.plugins = plugins.load(self)
        self.emit("boot", {})
        self.engine.start()
        self.link = ServerLink(self)
        if self.cfg["button"].get("enabled", True):
            self.button = Button(self.on_button, self.cfg["button"].get("long_press_ms", 1500))
        threading.Thread(target=self._telemetry, name="telemetry", daemon=True).start()

    def stop(self):
        self.emit("shutdown", {})
        for p in self.plugins.values():
            try:
                p.stop()
            except Exception:
                pass
        if self.link:
            self.link.stop()
        self.engine.stop()

    def has_plugin(self, name):
        return name in self.plugins

    # ------------------------------------------------------------ event fan-out
    def emit(self, event, data):
        for p in list(self.plugins.values()):
            try:
                p.on_event(event, data)
            except Exception:
                log.exception("plugin %s failed on %s", p.name, event)
        if event == "meters":
            return          # the web UI polls meters separately
        msg = {"event": event, **data}
        for q in list(self.listeners):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    # ------------------------------------------------------------ engine side
    def on_engine_connect(self):
        self.engine.send("config", quiet=True, **self.cfg.engine())
        self.engine.send("link", up=bool(self.link and self.link.up))
        self.engine.send("kws_load", dir=self.cfg.wakeword_dir)
        self.engine.send("status")

    def on_engine(self, ev):
        name = ev.get("event")
        if name == "meters":
            self.meters = ev
            self.emit("meters", ev)
            return
        if name == "hello":
            self.engine_version = ev.get("version")
        elif name == "status":
            self.engine_status = ev
            self.keywords = ev.get("keywords", [])
            return
        elif name == "state":
            log.info("state: %s -> %s (%s)", self.state, ev["state"], ev.get("reason", ""))
            self.state = ev["state"]
            self.muted = bool(ev.get("muted"))
            self.emit("state", {"state": self.state, "muted": self.muted, "reason": ev.get("reason")})
        elif name == "wake":
            if ev.get("offline"):
                self.emit("error", {"reason": "offline"})
                return
            self.counters["wakes"] += 1
            self.streaming_wake = ev["wake_id"]
            self.emit("wake", ev)
            self._send(dict(ev, type="wake", event=None))
            return
        elif name == "uplink_end":
            self.streaming_wake = None
        elif name == "stream":
            self._send({"type": "playback", "id": ev["id"], "kind": ev["kind"], "what": ev["what"],
                        "ms": ev.get("ms", 0)})
            return
        elif name == "kws_loaded":
            self.keywords = ev.get("keywords", [])
            sug = ev.get("suggested_threshold")
            if sug and self.cfg["wakeword"].get("auto_threshold", True) and ev.get("templates", 0) >= 2:
                self.apply_engine({"kws_threshold": round(sug, 3)})
            self._send({"type": "wake_words", "wake_words": self.wake_words()})
            self.emit("kws_loaded", ev)
            return
        elif name in ("record_done", "kws_check"):
            r = self.records.get((name, ev.get("path")))
            if r:
                r["result"] = ev
                r["done"].set()
            return
        elif name == "kws_negative":
            self._prune_negatives()
            self.emit("kws_negative", ev)
            return
        elif name == "engine_down":
            self.emit("error", {"reason": "engine"})
        if name in FORWARD:
            msg = {k: v for k, v in ev.items() if k != "event"}
            msg["type"] = FORWARD[name]
            self._send(msg)

    def on_uplink(self, pcm):
        if self.streaming_wake is not None and self.link and self.link.up:
            self.link.send_audio(pcm)

    def on_monitor(self, pcm):
        for q in list(self.monitor_listeners):
            try:
                q.put_nowait(pcm)
            except queue.Full:
                pass

    def wake_words(self):
        return [k["name"] for k in self.keywords]

    # ------------------------------------------------------------ server side
    def _send(self, msg):
        msg = {k: v for k, v in msg.items() if v is not None}
        if self.link:
            self.link.send(msg)

    def on_link(self, up, info):
        self.engine.send("link", up=up)
        status = "online" if up else info.get("status", "offline")
        self.emit("link", {"up": up, "status": status})
        if up:
            remote = info.get("config") or {}
            if remote:
                self.apply_remote_config(remote)
            threading.Thread(target=self._sync_samples, name="sync-samples", daemon=True).start()

    def _sync_samples(self):
        """Hands the server our enrollment recordings (it keeps one copy of each)
        so satellites without an enrollment of their own can use them too."""
        root = self.cfg.wakeword_dir
        try:
            keywords = sorted(os.listdir(root))
        except OSError:
            return
        for kw in keywords:
            d = os.path.join(root, kw)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.endswith(".wav"):
                    continue                    # negatives/ is a folder: skipped
                try:
                    with open(os.path.join(d, name), "rb") as f:
                        self._send({"type": "wake_sample", "keyword": kw, "sync": True,
                                    "wav_b64": base64.b64encode(f.read()).decode()})
                except OSError:
                    pass

    def on_server_audio(self, stream_id, pcm):
        self.engine.downlink(stream_id, pcm)

    def on_server(self, m):
        t = m.get("type")
        if t == "cancel":
            log.info("server: cancel (%s)", m.get("reason"))
            if m.get("wake_id") in (None, self.streaming_wake) or self.state != "idle":
                if m.get("reason") == "rejected":
                    self.counters["rejected"] += 1
                    # learn from it: this audio is not the wake word
                    if self.cfg["wakeword"].get("learn_negatives", True) and \
                            (m.get("verify_score") is None or m["verify_score"] < 0.6):
                        self.engine.send("kws_negative")
                self.engine.send("cancel", reason=m.get("reason", "server"))
        elif t == "eot":
            log.info("server: end of turn")
            self.engine.send("eot")
        elif t == "stream_open":
            at = m.get("start_at_ns")
            self.engine.send("stream_open", id=m["id"], kind=m.get("kind", "tts"),
                             rate=m.get("rate", 24000), channels=m.get("channels", 1),
                             start_at_ns=self.link.server_to_local_ns(at) if at else 0,
                             gain_db=m.get("gain_db", 0), prebuffer_ms=m.get("prebuffer_ms", -1))
        elif t == "stream_close":
            self.engine.send("stream_close", id=m["id"], drain=m.get("drain", True))
        elif t == "stream_pause":
            self.engine.send("stream_pause", id=m["id"], paused=m.get("paused", True))
        elif t == "session_end":
            log.info("server: session end (follow-up %s)", m.get("follow_up", False))
            self.counters["turns"] += 1
            self.engine.send("session_end", follow_up=m.get("follow_up", False),
                             follow_up_ms=m.get("follow_up_ms", 6000))
        elif t == "listen":
            self.engine.send("listen", timeout_ms=m.get("timeout_ms", 6000), earcon=m.get("earcon", False))
        elif t == "stop":
            self.engine.send("stop", media=m.get("media", True))
            self._alarm(False)
        elif t == "earcon":
            self.engine.send("earcon", name=m.get("name", "notify"), loop=m.get("loop", False))
        elif t == "alarm":
            self._alarm(bool(m.get("on")))
        elif t == "set":
            self.apply_engine({k: m[k] for k in ("volume", "mic_muted", "speaker_muted") if k in m})
        elif t == "config":
            self.apply_remote_config(m)
        elif t == "led":
            self.emit("led", m)
        elif t == "transcript":
            self.last_transcript = m.get("text", "")
            self.emit("transcript", {"text": self.last_transcript, "final": m.get("final", True)})
        elif t == "reply":
            self.last_reply = m.get("text", "")
            self.emit("reply", {"text": self.last_reply})
        elif t == "wake_ack":
            pass
        elif t == "error":
            log.warning("server error: %s", m.get("message"))
            self.emit("error", {"reason": m.get("message", "server")})

    def _alarm(self, on):
        if on == self.alarm_on:
            return
        self.alarm_on = on
        if on:
            self.engine.send("earcon", name="alarm", loop=True)
        else:
            self.engine.send("stop", media=False)
        self.emit("alarm", {"on": on})

    def apply_remote_config(self, m):
        changes = {}
        for k in ("name", "room"):
            if k in m and m[k] != self.cfg[k]:
                changes[k] = m[k]
        if changes:
            self.cfg.update(changes)
        if isinstance(m.get("engine"), dict):
            self.apply_engine(m["engine"])

    # ------------------------------------------------------------ settings
    def apply_engine(self, changes):
        """Applies engine settings now, persists them and tells everyone."""
        if not changes:
            return
        old = self.cfg.engine()
        self.cfg.update({"engine": changes})
        self.engine.send("config", **changes)
        if "volume" in changes and changes["volume"] != old.get("volume"):
            self.emit("volume", {"volume": changes["volume"]})
            self.engine.send("earcon", name="volume")
        if "mic_muted" in changes and bool(changes["mic_muted"]) != bool(old.get("mic_muted")):
            self.muted = bool(changes["mic_muted"])
            self.emit("mute", {"muted": self.muted})
        shared = {k: changes[k] for k in ("volume", "mic_muted", "speaker_muted") if k in changes}
        if shared:
            self._send({"type": "settings", **shared})

    def on_button(self, kind):
        log.info("button: %s press", kind)
        if kind == "long":
            self.apply_engine({"mic_muted": not self.muted})
            return
        # short press: stop what is playing, cancel a turn, or start one
        if self.alarm_on or self.state == "speaking":
            self.engine.send("stop", media=False)
            self._alarm(False)
            self._send({"type": "button", "action": "stop"})
        elif self.state in ("listening", "thinking"):
            self.engine.send("cancel", reason="button")
            self._send({"type": "button", "action": "cancel"})
        else:
            self.engine.send("wake", source="button")

    # ------------------------------------------------------------ wake words
    def wakeword_samples(self):
        out = []
        base = self.cfg.wakeword_dir
        for kw in sorted(os.listdir(base)):
            d = os.path.join(base, kw)
            if os.path.isdir(d):
                nd = os.path.join(d, "negatives")
                out.append({"name": kw, "samples": sorted(f for f in os.listdir(d) if f.endswith(".wav")),
                            "negatives": len(os.listdir(nd)) if os.path.isdir(nd) else 0})
        return out

    @staticmethod
    def safe_name(name):
        name = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
        return name[:40]

    def _engine_call(self, event, path, cmd, timeout, **fields):
        r = {"done": threading.Event(), "result": None}
        self.records[(event, path)] = r
        self.engine.send(cmd, path=path, **fields)
        r["done"].wait(timeout)
        self.records.pop((event, path), None)
        return r["result"]

    def record_sample(self, keyword, ms=2500):
        """Records one enrollment sample and checks it. Returns (name, report);
        an unusable sample is deleted and reported as such."""
        keyword = self.safe_name(keyword)
        if not keyword:
            raise ValueError("invalid wake word name")
        d = os.path.join(self.cfg.wakeword_dir, keyword)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "sample_%d.wav" % int(time.time() * 1000))
        res = self._engine_call("record_done", path, "record", ms / 1000 + 3, ms=ms)
        if not res or not res.get("ok"):
            raise RuntimeError("recording failed (engine not running?)")
        chk = self._engine_call("kws_check", path, "kws_check", 5) or {}
        report = {"usable": bool(chk.get("usable")), "cost": chk.get("cost"),
                  "start_ms": chk.get("start_ms"), "end_ms": chk.get("end_ms"), "advice": ""}
        others = len([f for f in os.listdir(d) if f.endswith(".wav")]) - 1
        if not report["usable"] or (report["start_ms"] or 0) < 40:
            report["usable"] = False
            report["advice"] = ("I heard nothing clear: say it right after the beep, a little louder."
                                if not chk.get("usable") else
                                "You started during the beep: wait for it to end, then say it.")
        elif (report["end_ms"] or 0) > ms - 150:
            report["usable"] = False
            report["advice"] = "It was cut at the end: say it right after the beep, without pausing."
        elif others >= 2 and (report["cost"] or 9) > 0.5:
            report["usable"] = False
            report["advice"] = ("This one doesn't sound like your other samples (%.2f). Say the same phrase "
                                "the same way." % report["cost"])
        if not report["usable"]:
            os.remove(path)
            self.reload_wakewords()
            return None, report
        self.reload_wakewords()
        # let the server learn how its speech recognizer hears this wake word
        try:
            with open(path, "rb") as f:
                self._send({"type": "wake_sample", "keyword": keyword,
                            "wav_b64": base64.b64encode(f.read()).decode()})
        except OSError:
            pass
        return os.path.basename(path), report

    def _prune_negatives(self, keep=20):
        base = self.cfg.wakeword_dir
        for kw in os.listdir(base):
            nd = os.path.join(base, kw, "negatives")
            if os.path.isdir(nd):
                files = sorted(f for f in os.listdir(nd) if f.endswith(".wav"))
                for f in files[:-keep]:
                    os.remove(os.path.join(nd, f))

    def delete_sample(self, keyword, sample):
        keyword = self.safe_name(keyword)
        d = os.path.join(self.cfg.wakeword_dir, keyword)
        if sample:
            p = os.path.join(d, os.path.basename(sample))
            if os.path.exists(p):
                os.remove(p)
        if os.path.isdir(d) and (not sample or not [f for f in os.listdir(d) if f.endswith(".wav")]):
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        self.reload_wakewords()

    def reload_wakewords(self):
        self.engine.send("kws_load", dir=self.cfg.wakeword_dir)

    # ------------------------------------------------------------ telemetry
    def _telemetry(self):
        system.cpu_percent()
        while True:
            time.sleep(30)
            m = self.meters
            snap = system.snapshot()
            self._send({"type": "telemetry", "system": snap, "counters": self.counters,
                        "engine": {"cpu": m.get("cpu"), "xruns": m.get("xruns"), "snr_db": m.get("snr_db"),
                                   "noise_db": m.get("noise_db"), "erle_db": m.get("erle_db")},
                        "rtt_ms": self.link.rtt_ms if self.link else None})
