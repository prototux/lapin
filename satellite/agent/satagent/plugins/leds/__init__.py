"""LED ring plugin: shows the assistant's state on the 12 APA102 LEDs.

Optional: disable it with config["plugins"]["leds"]["enabled"] = false, or
delete this folder; nothing else depends on it.
"""

import logging
import math
import threading
import time

from .. import PluginBase
from . import anim
from .ring import N, Apa102, LedPower, NullStrip

log = logging.getLogger("leds")

DEFAULTS = {
    "enabled": True,
    "brightness": 60,       # 0..100
    "palette": "aurora",
    "idle": "off",          # off | ambient
    "offset": 0,            # physical LED at 0 degrees (toward the Ethernet edge)
    "reverse": False,       # LEDs numbered counter-clockwise
    "show_offline": True,
    "fps": 50,
}


class Plugin(PluginBase):
    name = "leds"
    title = "LED ring"
    description = "State, direction and voice activity on the 12-LED ring."

    def __init__(self, agent, settings):
        super().__init__(agent, {**DEFAULTS, **settings})
        self.ctx = anim.Ctx()
        self.anim = anim.Animator(self.ctx)
        self.frame = anim.blank()
        self.stopping = threading.Event()
        self.state, self.muted, self.link = "idle", False, "offline"
        self.alarm_on, self.notify_on = False, False
        self.test_until, self.test_base = 0, None
        self._doa_target, self._level_target, self._out_target = 0.0, 0.0, 0.0
        self.power = None
        self.strip = NullStrip()
        self.hw_error = None
        self._apply()

    # ------------------------------------------------------------ lifecycle
    def start(self):
        try:
            self.power = LedPower()
            self.power.on()
            self.strip = Apa102()
        except Exception as e:      # no hardware: keep running for the preview
            self.hw_error = str(e)
            log.warning("LED ring unavailable (%s): preview only", e)
            self.strip = NullStrip()
        self._update_base()
        threading.Thread(target=self._run, name="leds", daemon=True).start()

    def stop(self):
        self.stopping.set()
        time.sleep(0.05)
        try:
            self.strip.show(anim.blank(), 0)
        except OSError:
            pass
        if self.power:
            self.power.off()

    def _apply(self):
        s = self.settings
        self.ctx.pal = anim.PALETTES.get(s["palette"], anim.PALETTES["aurora"])
        self.ctx.idle_mode = s["idle"]
        self.map = [(int(s["offset"]) + (-i if s["reverse"] else i)) % N for i in range(N)]

    def set_settings(self, changes):
        for k, v in changes.items():
            if k in DEFAULTS:
                self.settings[k] = type(DEFAULTS[k])(v)
        self._apply()
        self._update_base()
        return self.get_settings()

    # ------------------------------------------------------------ rendering
    def _run(self):
        last = time.monotonic()
        while not self.stopping.is_set():
            now = time.monotonic()
            dt = min(now - last, 0.1)
            last = now
            c = self.ctx
            # smooth the inputs (meters come at ~12 Hz)
            d = anim.signed(self._doa_target, c.doa)
            c.doa = (c.doa + d * (1 - math.exp(-dt / 0.12))) % N
            k = 0.06 if self._level_target > c.level else 0.25
            c.level += (self._level_target - c.level) * (1 - math.exp(-dt / k))
            k = 0.03 if self._out_target > c.out else 0.18
            c.out += (self._out_target - c.out) * (1 - math.exp(-dt / k))
            if self.test_base and now > self.test_until:
                self.test_base = None
                self._update_base()
            # safety net: never show "thinking" for long without news
            if self.anim.base == "thinking" and not self.test_base and self.anim.base_t > 20:
                log.warning("thinking for 20 s without a state change: back to idle")
                self.state = "idle"
                self._update_base()
            self.frame = self.anim.render(dt)
            physical = [anim.BLACK] * N
            for i, px in enumerate(self.frame):
                physical[self.map[i]] = px
            try:
                self.strip.show(physical, (self.settings["brightness"] / 100) ** 2.2)
            except OSError as e:
                log.warning("LED write failed: %s", e)
                self.stopping.wait(1)
            self.stopping.wait(max(0.0, 1 / self.settings["fps"] - (time.monotonic() - now)))

    def _update_base(self):
        if self.test_base:
            return self.anim.set_base(self.test_base)
        if self.alarm_on:
            base = "alarm"
        elif self.state in ("listening", "thinking", "speaking"):
            base = self.state
        elif self.muted:
            base = "muted"
        elif self.link == "pending":
            base = "pending"
        elif self.link != "online" and self.settings["show_offline"]:
            base = "offline"
        elif self.notify_on:
            base = "notify"
        else:
            base = "idle"
        self.anim.set_base(base)

    # ------------------------------------------------------------ events
    def on_event(self, event, data):
        if event == "boot":
            self.anim.overlay("boot")
        elif event == "state":
            self.state = data.get("state", "idle")
            self.muted = bool(data.get("muted", self.muted))
            self._update_base()
        elif event == "wake":
            if data.get("doa") is not None:
                self._doa_target = (float(data["doa"]) / 360 * N) % N
                self.ctx.doa = self._doa_target
            self.anim.overlay("wake")
        elif event == "meters":
            if getattr(self, "_demo_running", False):
                return
            if self.state == "listening" and data.get("track_valid"):
                self._doa_target = (float(data["track"]) / 360 * N) % N
            snr = data.get("snr_db", 0) if data.get("speech") else 0
            self._level_target = anim.clamp(snr / 22)
            self._out_target = anim.clamp((data.get("out_db", -90) + 50) / 38)
        elif event == "link":
            self.link = data.get("status", "online" if data.get("up") else "offline")
            self._update_base()
        elif event == "volume":
            self.ctx.volume = data.get("volume", self.ctx.volume)
            self.anim.overlay("volume", self.ctx.volume)
        elif event == "mute":
            self.muted = bool(data.get("muted"))
            self.anim.overlay("mute", self.muted)
            self._update_base()
        elif event == "error":
            self.anim.overlay("error")
        elif event == "alarm":
            self.alarm_on = bool(data.get("on"))
            self._update_base()
        elif event == "led":
            p = data.get("pattern")
            if p in anim.OVERLAYS:
                self.anim.overlay(p, data.get("arg"))
            elif p == "notify":
                self.notify_on = bool(data.get("on", True))
                self._update_base()
        elif event == "shutdown":
            self.stop()

    # ------------------------------------------------------------ demo
    def _demo(self):
        """A whole interaction with simulated voice and playback levels, to
        judge the animations on the real ring (and in the web preview)."""
        self._demo_running = True
        a, wait = self.anim, self.stopping.wait
        try:
            def base(name, seconds):
                self.test_base, self.test_until = name, time.monotonic() + seconds + 1
                a.set_base(name)
            base("idle", 2)
            a.overlay("boot")
            wait(2.5)
            self._doa_target = self.ctx.doa = 3.0          # talker at 90 degrees
            a.overlay("wake")
            base("listening", 4)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 4:                 # speech with syllables, talker walks
                t = time.monotonic() - t0
                self._level_target = max(0.0, math.sin(t * 9) * 0.5 + 0.4) if 0.4 < t < 3.2 else 0.0
                self._doa_target = (3.0 + t * 0.6) % N
                wait(0.05)
            self._level_target = 0
            base("thinking", 3)
            wait(3)
            base("speaking", 4.5)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 4.5:
                t = time.monotonic() - t0
                self._out_target = max(0.0, 0.55 + 0.45 * math.sin(t * 7.3) * math.sin(t * 1.9))
                wait(0.04)
            self._out_target = 0
            base("idle", 1.5)
            wait(1.5)
            for name, arg, pause in (("volume", 70, 2.2), ("mute", True, 0.8)):
                a.overlay(name, arg)
                wait(pause)
            base("muted", 2)
            wait(2)
            a.overlay("mute", False)
            base("idle", 1)
            wait(1)
            for name, secs in (("alarm", 3), ("notify", 3), ("offline", 4), ("pending", 3)):
                base(name, secs)
                wait(secs)
            base("idle", 0.5)
            a.overlay("success")
            wait(1.2)
            a.overlay("error")
            wait(1)
        finally:
            self.test_base = None
            self._demo_running = False
            self._update_base()

    # ------------------------------------------------------------ web API
    def status(self):
        return {"hardware": self.hw_error is None, "error": self.hw_error, "base": self.anim.base}

    def api(self, action, data):
        if action == "preview":
            return {"frame": ["#%02x%02x%02x" % tuple(round(v * 255) for v in px) for px in self.frame],
                    "base": self.anim.base, "doa": self.ctx.doa * 360 / N}
        if action == "test":
            name = data.get("name")
            if name in anim.BASES:
                self.test_base = name
                self.test_until = time.monotonic() + float(data.get("seconds", 6))
                if name == "listening":
                    self._level_target = 0.0
                self.anim.set_base(name)
            elif name in anim.OVERLAYS:
                self.anim.overlay(name, data.get("arg"))
            else:
                return {"error": "unknown animation"}
            return {"ok": True}
        if action == "demo":
            if not getattr(self, "_demo_running", False):
                threading.Thread(target=self._demo, daemon=True).start()
            return {"ok": True}
        if action == "catalog":
            return {"bases": list(anim.BASES), "overlays": list(anim.OVERLAYS),
                    "palettes": {k: ["#%02x%02x%02x" % tuple(round(v * 255) for v in c) for c in p]
                                 for k, p in anim.PALETTES.items()}}
        return super().api(action, data)
