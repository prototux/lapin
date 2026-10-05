"""Proactive / notification engine: fires timers, alarms and reminders on the
right device (the one that set them, else its room, else everywhere), or
as a message when they were set from a chat. Respects quiet hours."""

import datetime as dt
import logging
import threading
import time

from . import timeparse as tp
from .lang import detect, say

log = logging.getLogger("notify")
RING_SECONDS = 60


class Notifier:
    def __init__(self, app):
        self.app = app
        self.ev = threading.Event()
        self.ringing = {}           # device id -> stop time
        self.lock = threading.Lock()
        threading.Thread(target=self._run, name="notifier", daemon=True).start()

    def reschedule(self):
        self.ev.set()

    def in_quiet_hours(self):
        q = self.app.settings["quiet_hours"]
        if not q.get("enabled"):
            return False
        now = tp.now(self.app.settings).strftime("%H:%M")
        a, b = q.get("start", "22:30"), q.get("end", "07:00")
        return a <= now or now < b if a > b else a <= now < b

    def _run(self):
        while True:
            items = self.app.store.active_timers()
            now = time.time()
            due = [t for t in items if t["due"] <= now + 0.05]
            for t in due:
                try:
                    self._fire(t)
                except Exception:
                    log.exception("firing %s failed", t)
            self._expire_rings()
            nxt = min([t["due"] for t in items if t["due"] > now] + [now + 1.0])
            self.ev.wait(max(0.05, min(nxt - time.time(), 1.0)))
            self.ev.clear()

    def _fire(self, t):
        app = self.app
        kind, label = t["kind"], t["label"] or ""
        if t["repeat"]:
            nxt = self._next_repeat(t)
            app.store.x("UPDATE timers SET due = ? WHERE id = ?", (nxt, t["id"]))
        else:
            app.store.set_timer_status(t["id"], "fired")
        L = app.settings["assistant"].get("language", "auto")
        if L not in ("en", "fr"):
            L = detect(label, app.settings["assistant"].get("default_language", "en")) if label else \
                app.settings["assistant"].get("default_language", "en")
        if kind == "timer":
            text = say("timer_done_label", L, l=label) if label else say("timer_done", L)
        elif kind == "alarm":
            text = say("alarm", L, t=tp.say_time(tp.now(app.settings), L)) + (" " + label + "." if label else "")
        else:
            text = say("reminder", L, x=label.rstrip("."))
        app.bus.publish("notify", kind=kind, text=text, timer=t["id"])
        log.info("%s fired: %s", kind, text)

        # set from a chat: answer there
        if t["channel"] not in ("voice", "browser") and app.messaging:
            if app.messaging.send(t["channel"], t["chat_id"], ("⏰ " if kind != "reminder" else "🔔 ") + text):
                return
        dev = app.store.device(t["device_id"]) if t["device_id"] else None
        targets = app.router.targets_for(t["device_id"], (dev or {}).get("room", ""))
        if not targets:
            if app.messaging and t["user"]:
                app.messaging.send_to_user(t["user"], text)
            return
        quiet = self.in_quiet_hours()
        if kind == "reminder":
            if quiet and app.messaging and app.messaging.send_to_user(t["user"], text)[0]:
                return
            for s in targets:
                s.send({"type": "earcon", "name": "notify"})
                s.send({"type": "led", "pattern": "notify", "on": True})
            app.router.announce(text, targets, chime=False, gain_db=-8 if quiet else 0)
            threading.Timer(20, lambda: [s.send({"type": "led", "pattern": "notify", "on": False})
                                          for s in targets]).start()
            return
        # timers and alarms ring until stopped (voice "stop", the button, or a timeout)
        for s in targets:
            self.ring(s, text)

    def ring(self, session, text):
        with self.lock:
            self.ringing[session.id] = time.time() + RING_SECONDS
        session.send({"type": "alarm", "on": True})

        def speak_later():
            time.sleep(2.2)
            if session.id in self.ringing:
                self.app.router.speak(session, text)
        threading.Thread(target=speak_later, daemon=True).start()

    def stop_ringing(self, device_id):
        with self.lock:
            was = self.ringing.pop(device_id, None)
        s = self.app.devices.get(device_id)
        if s and was:
            s.send({"type": "alarm", "on": False})
        return bool(was)

    def _expire_rings(self):
        now = time.time()
        with self.lock:
            done = [d for d, until in self.ringing.items() if until < now]
        for d in done:
            self.stop_ringing(d)

    def _next_repeat(self, t):
        due = dt.datetime.fromtimestamp(t["due"], tp.tz(self.app.settings))
        while True:
            due += dt.timedelta(days=1)
            wd = due.weekday()
            if t["repeat"] == "daily" or (t["repeat"] == "weekdays" and wd < 5) or \
                    (t["repeat"] == "weekends" and wd >= 5):
                return due.timestamp()
