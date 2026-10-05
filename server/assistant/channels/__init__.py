"""Messaging channels: optional, self-contained modules.

Each module defines a Channel subclass. A channel only runs when enabled in
the settings (channels.<name>.enabled), so a household that does not want
Telegram or Signal simply leaves them off. Identity linking is per channel:
channels.<name>.users maps an external id (Telegram user id, Signal UUID or
number...) to a household user; unknown senders are listed in the admin UI
so they can be linked in one click.
"""

import importlib
import logging
import pkgutil
import time

log = logging.getLogger("channels")


class Channel:
    name = "base"
    title = "Channel"
    description = ""
    # settings fields for the admin UI: (key, label, type) with type text / secret / bool
    FIELDS = []
    DEFAULTS = {"enabled": False, "users": {}}

    def __init__(self, app, hub):
        self.app = app
        self.hub = hub
        self.status_text = "disabled"
        self.unknown = {}           # external id -> {name, ts, text}

    @property
    def cfg(self):
        return {**self.DEFAULTS, **self.app.settings["channels"].get(self.name, {})}

    def enabled(self):
        return bool(self.cfg.get("enabled"))

    def update_cfg(self, changes):
        self.app.settings.update({"channels": {self.name: changes}})

    # lifecycle: start() is called once; implementations check enabled() in
    # their loop so they follow the switch without a restart
    def start(self):
        pass

    def status(self):
        return self.status_text if self.enabled() else "disabled"

    # identities
    def user_for(self, external_id):
        return self.cfg.get("users", {}).get(str(external_id))

    def link(self, external_id, user):
        users = dict(self.cfg.get("users", {}))
        if user:
            users[str(external_id)] = user
            self.unknown.pop(str(external_id), None)
        else:
            users.pop(str(external_id), None)
        self.update_cfg({"users": users})
        return users

    def note_unknown(self, external_id, name, text=""):
        self.unknown[str(external_id)] = {"name": name or "", "ts": time.time(), "text": (text or "")[:80]}
        self.app.bus.publish("channel_unknown", channel=self.name, sender=str(external_id), name=name)

    # messages
    def send(self, chat_id, text):
        return False

    def send_to_user(self, user, text):
        for ext, u in self.cfg.get("users", {}).items():
            if u.lower() == (user or "").lower():
                return self.send(ext, text)
        return False

    def api(self, action, data):
        return {"error": "unknown action"}

    def view(self):
        cfg = self.cfg
        return {"name": self.name, "title": self.title, "description": self.description,
                "enabled": self.enabled(), "status": self.status(),
                "fields": [{"key": k, "label": l, "type": t,
                            "value": ("********" if t == "secret" and cfg.get(k) else cfg.get(k, ""))}
                           for k, l, t in self.FIELDS],
                "users": cfg.get("users", {}),
                "unknown": [{"id": k, **v} for k, v in self.unknown.items()]}


def load(app, hub):
    out = {}
    for m in pkgutil.iter_modules(__path__):
        try:
            mod = importlib.import_module("%s.%s" % (__name__, m.name))
            if hasattr(mod, "CHANNEL"):
                ch = mod.CHANNEL(app, hub)
                out[ch.name] = ch
        except Exception:
            log.exception("channel %s failed to load", m.name)
    return out
