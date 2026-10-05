"""Messaging gateway: the same assistant over text channels (web chat,
Telegram, Signal...). Channels are optional modules (assistant/channels),
each enabled or not in the settings. Chat turns go through the orchestrator
like voice turns, answered as text (or as a voice note for voice notes)."""

import logging

from . import channels as channel_modules
from .orchestrator import TurnContext
from .trace import Trace

log = logging.getLogger("messaging")


class Messaging:
    def __init__(self, app):
        self.app = app
        self._migrate()
        self.channels = channel_modules.load(app, self)

    def _migrate(self):
        """Old settings layout: a top-level "telegram" section."""
        s = self.app.settings
        old = s.overrides.get("telegram")
        if old and "telegram" not in s.overrides.get("channels", {}):
            s.update({"channels": {"telegram": {"enabled": bool(old.get("token")), "token": old.get("token", ""),
                                                "users": old.get("allowed", {}),
                                                "voice_replies": old.get("voice_replies", True)}}})

    def start(self):
        for ch in self.channels.values():
            try:
                ch.start()
            except Exception:
                log.exception("channel %s failed to start", ch.name)

    @property
    def web(self):
        return self.channels["web"]

    @property
    def web_history(self):
        return self.web.history

    def status(self):
        return {n: ch.status() for n, ch in self.channels.items()}

    # ------------------------------------------------------------ turns
    def handle_text(self, channel, chat_id, user, text, voice=False):
        """Runs one chat turn; returns the reply text."""
        app = self.app
        tr = Trace(channel)
        tr.event("message", channel=channel, voice=voice, text=text)
        ctx = TurnContext(app, channel=channel, user=user, chat_id=str(chat_id), trace=tr)
        parts, info = [], {}
        for kind, val in app.orchestrator.handle(ctx, text):
            if kind == "sentence":
                if not parts:
                    tr.mark("first_sentence")
                parts.append(val)
            elif kind == "done":
                info = val
        reply = info.get("text") or " ".join(parts)
        if not reply and ctx.silent:
            reply = "Done." if ctx.lang != "fr" else "C'est fait."
        tr.mark("done")
        keep = app.settings["privacy"].get("store_transcripts", True)
        turn = {"id": tr.id, "ts": tr.t0, "device_id": "", "channel": channel, "user": user,
                "transcript": text if keep else "", "reply": reply if keep else "", "route": info.get("route", ""),
                "status": "done", "trace": tr.to_dict()}
        app.store.save_turn(turn)
        app.bus.publish("turn", id=tr.id, device=channel, status="done", transcript=turn["transcript"],
                        reply=turn["reply"], route=turn["route"], trace=turn["trace"])
        return reply

    # ------------------------------------------------------------ push
    def can_send(self):
        return any(ch.enabled() and ch.cfg.get("users") for n, ch in self.channels.items() if n != "web")

    def send(self, channel, chat_id, text):
        ch = self.channels.get(channel)
        return bool(ch and ch.enabled() and ch.send(chat_id, text))

    def send_to_user(self, user, text):
        """On the first enabled channel where the user is linked, else the web chat."""
        for n, ch in self.channels.items():
            if n != "web" and ch.enabled() and ch.send_to_user(user, text):
                return True, "sent on %s" % ch.title
        if self.web.enabled():
            self.web.send(user, text)
            return True, "posted in the web chat (%s is not linked on a messaging app)" % user
        return False, "no channel to reach %s" % user
