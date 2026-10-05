"""Telegram bot (long polling: no public URL needed). Text and voice notes;
voice notes are transcribed and can be answered with a voice note."""

import collections
import logging
import threading
import time

import requests

from . import Channel
from ..audio import decode_to_pcm16k, pcm_to_ogg_opus
from ..composer import for_speech
from ..lang import detect

log = logging.getLogger("telegram")


class Telegram(Channel):
    name = "telegram"
    title = "Telegram"
    description = "A Telegram bot (create one with @BotFather). Members link their Telegram id below."
    FIELDS = [("token", "Bot token (from @BotFather)", "secret"),
              ("voice_replies", "Answer voice notes with a voice note", "bool")]
    DEFAULTS = {"enabled": False, "users": {}, "token": "", "voice_replies": True}
    API = "https://api.telegram.org"

    def __init__(self, app, hub):
        super().__init__(app, hub)
        self.offset = 0
        self.http = requests.Session()
        self.seen = collections.deque(maxlen=500)

    def _url(self, method):
        return "%s/bot%s/%s" % (self.API, self.cfg["token"], method)

    def start(self):
        threading.Thread(target=self._run, name="telegram", daemon=True).start()

    def _run(self):
        while True:
            if not self.enabled() or not self.cfg.get("token"):
                self.status_text = "not configured" if self.enabled() else "disabled"
                time.sleep(3)
                continue
            try:
                r = self.http.get(self._url("getUpdates"), params={"timeout": 25, "offset": self.offset},
                                  timeout=35)
                data = r.json()
                if not data.get("ok"):
                    self.status_text = "error: %s" % data.get("description")
                    time.sleep(10)
                    continue
                self.status_text = "online"
                for upd in data.get("result", []):
                    self.offset = upd["update_id"] + 1
                    if upd["update_id"] in self.seen:
                        continue
                    self.seen.append(upd["update_id"])
                    if upd.get("message"):
                        threading.Thread(target=self._handle, args=(upd["message"],), daemon=True).start()
            except Exception as e:
                self.status_text = "error: %s" % str(e)[:100]
                time.sleep(5)

    def _handle(self, msg):
        app = self.app
        chat = msg["chat"]["id"]
        frm = msg.get("from", {})
        sender = str(frm.get("id", ""))
        user = self.user_for(sender)
        if not user:
            self.note_unknown(sender, frm.get("first_name", ""), msg.get("text", ""))
            self.send(chat, "Hi! I don't know you yet. Ask an admin to link your Telegram id %s." % sender)
            return
        voice = msg.get("voice") or msg.get("audio")
        try:
            if voice:
                self._action(chat, "typing")
                f = self.http.get(self._url("getFile"), params={"file_id": voice["file_id"]}, timeout=10).json()
                data = self.http.get("%s/file/bot%s/%s" % (self.API, self.cfg["token"], f["result"]["file_path"]),
                                     timeout=30).content
                text = app.stt.transcribe(decode_to_pcm16k(data))
                if not text:
                    self.send(chat, "I couldn't hear anything in that voice note.")
                    return
            else:
                text = msg.get("text", "")
                if not text or text.startswith("/start"):
                    self.send(chat, "Hi %s! Ask me anything." % user)
                    return
            self._action(chat, "typing")
            reply = self.hub.handle_text(self.name, chat, user, text, voice=bool(voice))
            if voice and self.cfg.get("voice_replies", True) and reply:
                pcm = app.tts.synthesize(for_speech(reply, detect(reply)), lang=detect(reply))
                self._send_voice(chat, pcm_to_ogg_opus(pcm, app.tts.RATE), caption=reply[:1000])
            else:
                self.send(chat, reply or "Done.")
        except Exception as e:
            log.exception("telegram message failed")
            self.send(chat, "Sorry, something went wrong: %s" % str(e)[:200])

    def _action(self, chat, action):
        try:
            self.http.post(self._url("sendChatAction"), json={"chat_id": chat, "action": action}, timeout=5)
        except Exception:
            pass

    def send(self, chat_id, text):
        if not self.enabled() or not self.cfg.get("token"):
            return False
        for i in range(3):
            try:
                r = self.http.post(self._url("sendMessage"), json={"chat_id": chat_id, "text": text[:4000]},
                                   timeout=10)
                if r.json().get("ok"):
                    return True
            except Exception:
                time.sleep(1 + i)
        return False

    def _send_voice(self, chat, ogg, caption=""):
        r = self.http.post(self._url("sendVoice"), data={"chat_id": chat, "caption": caption},
                           files={"voice": ("reply.ogg", ogg, "audio/ogg")}, timeout=30)
        return r.json().get("ok", False)


CHANNEL = Telegram
