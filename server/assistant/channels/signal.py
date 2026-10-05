"""Signal, through a signal-cli-rest-api container (MODE=json-rpc).

The assistant gets its own Signal account: Signal needs a phone number once,
to register (SMS or voice call: a landline or prepaid SIM is fine). Then the
account gets a hard-to-guess username, its number is hidden (not shared, not
discoverable), and household members add it as a contact by username / link
and message it. Senders are linked to household users like on Telegram.

    docker run -d --name signal-api -p 8080:8080 -e MODE=json-rpc \\
        -v signal-data:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
"""

import base64
import json
import logging
import secrets
import threading
import time

import requests
from websockets.sync.client import connect

from . import Channel
from ..audio import decode_to_pcm16k, pcm_to_ogg_opus
from ..composer import for_speech
from ..lang import detect

log = logging.getLogger("signal")


class Signal(Channel):
    name = "signal"
    title = "Signal"
    description = ("The assistant's own Signal account, found by a private username (its number stays "
                   "hidden). Needs a signal-cli-rest-api container (MODE=json-rpc).")
    FIELDS = [("api_url", "signal-cli-rest-api URL, e.g. http://127.0.0.1:8080", "text"),
              ("number", "Registered phone number (international format, +33...)", "text"),
              ("username", "Username (set with the button below)", "readonly"),
              ("username_link", "Contact link", "readonly"),
              ("profile_name", "Display name", "text"),
              ("voice_replies", "Answer voice notes with a voice note", "bool")]
    DEFAULTS = {"enabled": False, "users": {}, "api_url": "http://127.0.0.1:8080", "number": "", "username": "",
                "username_link": "", "profile_name": "Assistant", "voice_replies": True}

    def __init__(self, app, hub):
        super().__init__(app, hub)
        self.http = requests.Session()
        self.seen = set()

    def _url(self, path):
        return self.cfg["api_url"].rstrip("/") + path

    def start(self):
        threading.Thread(target=self._run, name="signal", daemon=True).start()

    # ------------------------------------------------------------ receive
    def _run(self):
        while True:
            cfg = self.cfg
            if not self.enabled() or not cfg.get("number"):
                self.status_text = "not configured (register a number below)" if self.enabled() else "disabled"
                time.sleep(3)
                continue
            ws_url = self._url("/v1/receive/%s" % requests.utils.quote(cfg["number"])) \
                .replace("http://", "ws://").replace("https://", "wss://")
            try:
                with connect(ws_url, open_timeout=8, ping_interval=30, max_size=32 * 1024 * 1024) as ws:
                    self.status_text = "online as %s" % (cfg.get("username") or cfg["number"])
                    while self.enabled():
                        try:
                            raw = ws.recv(timeout=5)
                        except TimeoutError:
                            continue
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        threading.Thread(target=self._handle, args=(msg,), daemon=True).start()
            except Exception as e:
                self.status_text = "error: %s" % str(e)[:120]
                time.sleep(8)

    def _handle(self, msg):
        env = msg.get("envelope") or msg.get("params", {}).get("envelope") or {}
        dm = env.get("dataMessage") or {}
        if not dm or env.get("timestamp") in self.seen:
            return
        self.seen.add(env.get("timestamp"))
        sender = env.get("sourceUuid") or env.get("source") or env.get("sourceNumber")
        reply_to = env.get("sourceNumber") or env.get("sourceUuid") or env.get("source")
        user = self.user_for(sender) or self.user_for(env.get("sourceNumber") or "")
        text = (dm.get("message") or "").strip()
        if not user:
            self.note_unknown(sender, env.get("sourceName", ""), text)
            self.send(reply_to, "Hi! I don't know you yet. Ask an admin to link this Signal account "
                                "(id %s) on the assistant's Integrations page." % sender)
            return
        voice = None
        for att in dm.get("attachments") or []:
            if (att.get("contentType") or "").startswith("audio/"):
                voice = att
                break
        try:
            if voice:
                data = self.http.get(self._url("/v1/attachments/%s" % voice["id"]), timeout=30).content
                text = self.app.stt.transcribe(decode_to_pcm16k(data))
                if not text:
                    self.send(reply_to, "I couldn't hear anything in that voice note.")
                    return
            if not text:
                return
            self._typing(reply_to)
            reply = self.hub.handle_text(self.name, reply_to, user, text, voice=bool(voice))
            if voice and self.cfg.get("voice_replies", True) and reply:
                pcm = self.app.tts.synthesize(for_speech(reply, detect(reply)), lang=detect(reply))
                ogg = base64.b64encode(pcm_to_ogg_opus(pcm, self.app.tts.RATE)).decode()
                self._send(reply_to, reply, ["data:audio/ogg;filename=reply.ogg;base64," + ogg])
            else:
                self.send(reply_to, reply or "Done.")
        except Exception as e:
            log.exception("signal message failed")
            self.send(reply_to, "Sorry, something went wrong: %s" % str(e)[:200])

    def _typing(self, to):
        try:
            self.http.put(self._url("/v1/typing-indicator/%s" % requests.utils.quote(self.cfg["number"])),
                          json={"recipient": to}, timeout=5)
        except Exception:
            pass

    # ------------------------------------------------------------ send
    def _send(self, to, text, attachments=None):
        body = {"number": self.cfg["number"], "recipients": [to], "message": text[:4000]}
        if attachments:
            body["base64_attachments"] = attachments
        for i in range(3):
            try:
                r = self.http.post(self._url("/v2/send"), json=body, timeout=20)
                if r.status_code < 300:
                    return True
                log.warning("signal send: %s %s", r.status_code, r.text[:200])
            except Exception as e:
                log.warning("signal send failed: %s", e)
            time.sleep(1 + i)
        return False

    def send(self, chat_id, text):
        if not self.enabled() or not self.cfg.get("number"):
            return False
        return self._send(str(chat_id), text)

    # ------------------------------------------------------------ setup (admin UI)
    def api(self, action, data):
        cfg = self.cfg
        num = (data.get("number") or cfg.get("number") or "").strip()
        q = requests.utils.quote(num)
        try:
            if action == "check":
                r = self.http.get(self._url("/v1/about"), timeout=5)
                accounts = self.http.get(self._url("/v1/accounts"), timeout=5).json()
                return {"ok": True, "about": r.json(), "accounts": accounts}
            if action == "register":
                # captcha: solve https://signalcaptchas.org/registration/generate.html and copy
                # the "signalcaptcha://..." link
                body = {"use_voice": bool(data.get("use_voice"))}
                if data.get("captcha"):
                    body["captcha"] = data["captcha"].strip()
                r = self.http.post(self._url("/v1/register/%s" % q), json=body, timeout=40)
                if r.status_code >= 300:
                    return {"error": r.text[:300]}
                self.update_cfg({"number": num})
                return {"ok": True, "message": "Code sent by %s to %s" % ("voice call" if body["use_voice"] else "SMS", num)}
            if action == "verify":
                code = (data.get("code") or "").replace("-", "").strip()
                r = self.http.post(self._url("/v1/register/%s/verify/%s" % (q, code)), json={}, timeout=40)
                if r.status_code >= 300:
                    return {"error": r.text[:300]}
                self._post_registration(num)
                return {"ok": True, "message": "Registered. Now set a username."}
            if action == "username":
                base = (data.get("base") or "assistant").strip().lower()
                name = "%s_%s" % (base, secrets.token_hex(4))     # hard to guess
                r = self.http.post(self._url("/v1/accounts/%s/username" % q), json={"username": name}, timeout=30)
                if r.status_code >= 300:
                    return {"error": r.text[:300]}
                res = r.json() if r.content else {}
                uname = res.get("username") or name
                link = res.get("username_link") or ""
                self.update_cfg({"username": uname, "username_link": link})
                return {"ok": True, "username": uname, "link": link}
            if action == "privacy":
                return self._post_registration(num)
        except requests.RequestException as e:
            return {"error": "cannot reach signal-cli-rest-api at %s: %s" % (cfg["api_url"], str(e)[:150])}
        return {"error": "unknown action"}

    def _post_registration(self, num):
        """Hide the number and set the display name."""
        q = requests.utils.quote(num)
        out = {}
        r = self.http.put(self._url("/v1/accounts/%s/settings" % q),
                          json={"discoverable_by_number": False, "share_number": False}, timeout=20)
        out["privacy"] = r.status_code < 300 or r.text[:200]
        r = self.http.put(self._url("/v1/profiles/%s" % q), json={"name": self.cfg.get("profile_name") or "Assistant"},
                          timeout=20)
        out["profile"] = r.status_code < 300 or r.text[:200]
        return {"ok": True, **out}


CHANNEL = Signal
