"""Web chat in the admin UI (and pushes to it)."""

import collections
import time

from . import Channel


class Web(Channel):
    name = "web"
    title = "Web chat"
    description = "Chat with the assistant from the admin web UI (Talk & chat page)."
    DEFAULTS = {"enabled": True, "users": {}}

    def __init__(self, app, hub):
        super().__init__(app, hub)
        self.history = collections.defaultdict(lambda: collections.deque(maxlen=200))
        self.status_text = "ready"

    def send(self, chat_id, text):
        if not self.enabled():
            return False
        self.history[chat_id].append({"from": "assistant", "text": text, "ts": time.time()})
        self.app.bus.publish("chat", chat=chat_id, text=text)
        return True

    def send_to_user(self, user, text):
        return self.send(user, text)


CHANNEL = Web
