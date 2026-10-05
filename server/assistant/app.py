"""The server application: builds and wires every service."""

import logging
import os
import secrets
import threading
import time

from .bus import Bus, BusLogHandler
from .clients import LLM, STT, TTS
from .config import Settings
from .devices import DeviceManager
from .gateway import Gateway
from .guardrails import Guardrails
from .media import MediaService
from .memory import Conversations
from .messaging import Messaging
from .notify import Notifier
from .orchestrator import Orchestrator
from .router import Router
from .sessions import TurnManager
from .skills import Registry
from .store import Store
from .verifier import Verifier
from .wakewords import WakeWords
from .firmware import DeviceLogs, Ota

log = logging.getLogger("app")


class App:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.started = time.time()
        self.settings = Settings(self.data_dir)
        self.bus = Bus()
        self.logs = BusLogHandler(self.bus)
        logging.getLogger().addHandler(self.logs)
        self.store = Store(os.path.join(self.data_dir, "assistant.db"))
        self.stt = STT(self.settings)
        self.tts = TTS(self.settings)
        self.llm = LLM(self.settings)
        self.conversations = Conversations(self.settings)
        self.devices = DeviceManager(self)
        self.router = Router(self)
        self.media = MediaService(self)
        self.messaging = Messaging(self)
        self.guardrails = Guardrails(self)
        self.skills = Registry(self)
        self.orchestrator = Orchestrator(self)
        self.verifier = Verifier(self)
        self.wakewords = WakeWords(self)
        self.device_logs = DeviceLogs(self)
        self.ota = Ota(self)
        self.turns = TurnManager(self)
        self.notifier = Notifier(self)
        self.gateway = Gateway(self)
        self.browser_secret = secrets.token_urlsafe(24)

    def start(self):
        self.gateway.start()
        self.messaging.start()
        threading.Thread(target=self._housekeeping, name="housekeeping", daemon=True).start()

    def voice_for(self, lang):
        t = self.settings["tts"]
        return (t.get("voice_fr") if lang == "fr" else "") or t["voice"]

    def health(self):
        return {"stt": self.stt.health, "tts": self.tts.health, "llm": self.llm.health,
                "channels": self.messaging.status()}

    def check_health(self):
        for c in (self.stt, self.tts, self.llm):
            c.check()
        return self.health()

    def _housekeeping(self):
        n = 0
        while True:
            try:
                self.check_health()
                if n % 60 == 0:     # hourly: retention
                    days = self.settings["privacy"].get("retention_days", 30)
                    for path in self.store.purge_turns(time.time() - days * 86400):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            except Exception:
                log.exception("housekeeping")
            n += 1
            time.sleep(60)
