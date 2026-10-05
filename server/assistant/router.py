"""Output router / playback orchestration: speech to the asking device,
announcements to rooms (synchronized start), alerts."""

import logging
import queue
import threading
import time

from .lang import detect

log = logging.getLogger("router")


class SpeechOut:
    """Speaks sentences on one device as they arrive: each sentence is
    synthesized (streaming) and sent while the next one is being generated.
    The stream opens on the first audio, so silent turns open nothing."""

    def __init__(self, app, session, trace=None, cancel=None, gain_db=0.0, lang_of=None, lang=None):
        self.app = app
        self.lang_of = lang_of or (lambda: lang or app.settings["assistant"].get("default_language", "en"))
        self.session = session
        self.trace = trace
        self.cancel = cancel or threading.Event()
        self.gain_db = gain_db
        self.q = queue.Queue()
        self.stream = None
        self.spoken = []
        self.error = None
        self.started_cb = None
        self.thread = threading.Thread(target=self._run, name="tts-%s" % session.id, daemon=True)
        self.thread.start()

    def say(self, text):
        if text and text.strip():
            self.q.put(text.strip())

    def _run(self):
        tts = self.app.tts
        while True:
            text = self.q.get()
            if text is None:
                break
            if self.cancel.is_set():
                continue
            t0 = time.time()
            try:
                first = True
                for pcm in tts.stream(text, voice=self.app.voice_for(self.lang_of()), cancel=self.cancel,
                                      lang=detect(text, self.lang_of(), min_words=4)):
                    if self.cancel.is_set():
                        break
                    if self.stream is None:
                        self.stream = self.session.open_stream("tts", tts.RATE, 1, gain_db=self.gain_db)
                        if self.trace:
                            self.trace.mark("tts_first_audio")
                            sid = self.stream.id
                            tr = self.trace
                            self.session.playback_cb[sid] = lambda m: m.get("what") == "started" and tr.mark("playback_started")
                    if first and self.trace:
                        self.trace.event("tts", chars=len(text), first_ms=round((time.time() - t0) * 1000))
                        first = False
                    self.stream.write(pcm, self.cancel)
                self.spoken.append(text)
            except Exception as e:
                self.error = str(e)
                log.warning("TTS failed: %s", e)
                if self.trace:
                    self.trace.event("error", where="tts", error=str(e)[:200])

    def finish(self, timeout=120):
        """Waits until everything has been synthesized and sent."""
        self.q.put(None)
        self.thread.join(timeout)
        if self.stream:
            self.stream.close(drain=not self.cancel.is_set())
        return bool(self.stream)

    def abort(self):
        self.cancel.set()
        self.q.put(None)
        if self.stream:
            self.stream.close(drain=False)


class Router:
    def __init__(self, app):
        self.app = app

    def speak(self, session, text, follow_up=False, gain_db=0.0, lang=None):
        """One-shot speech on a device, ending its session afterwards."""
        from .composer import calm
        out = SpeechOut(self.app, session, gain_db=gain_db, lang=lang or detect(text))
        out.say(text)
        out.finish()
        session.send({"type": "reply", "text": text})
        session.send({"type": "session_end", "follow_up": follow_up})

    def announce(self, text, sessions, chime=True, gain_db=0.0, lang=None):
        """Same message on several devices, starting together."""
        from .lang import detect

        def run():
            try:
                lang = lang or detect(text)
                pcm = self.app.tts.synthesize(calm(text), self.app.voice_for(lang), lang)
            except Exception as e:
                log.warning("announce: TTS failed: %s", e)
                return
            start = time.time() + 0.5 + (0.7 if chime else 0)
            for s in sessions:
                if chime:
                    s.send({"type": "earcon", "name": "notify"})
                st = s.open_stream("tts", self.app.tts.RATE, 1, start_at=start, gain_db=gain_db)
                st.write(pcm)
                st.close()
                s.send({"type": "session_end", "follow_up": False})
            self.app.bus.publish("announce", text=text, devices=[s.name for s in sessions])
        threading.Thread(target=run, name="announce", daemon=True).start()

    def targets_for(self, device_id, room=""):
        """Where to deliver something meant for a device: itself, else its
        room, else every satellite."""
        devs = self.app.devices
        s = devs.get(device_id) if device_id else None
        if s and s.alive:
            return [s]
        sats = devs.online("satellite")
        if room:
            same = [x for x in sats if x.room == room]
            if same:
                return same
        return sats
