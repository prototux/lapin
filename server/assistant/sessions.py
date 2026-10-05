"""Voice turns: wake arbitration, stage-2 verification, endpointing, ASR,
dialog, speech output.

    wake (device) ──► arbitration window: the best device keeps the turn,
                      the others are cancelled
                 ──► verifier on the pre-roll (in parallel)
    audio ──► VAD + semantic end of turn (a fast silence check confirmed by a
              transcript that reads complete, else a longer silence)
          ──► eot to the device ──► final ASR ──► orchestrator
          ──► sentences ──► streaming TTS ──► device ──► session_end
"""

import logging
import os
import re
import threading
import time

from .audio import Vad, denoise, wav_bytes
from .lang import say
from .orchestrator import TurnContext
from .router import SpeechOut
from .trace import Trace

log = logging.getLogger("turns")

BYTES_PER_MS = 32       # 16 kHz s16 mono
INCOMPLETE = set("and or but to for of the a an with at in on my your our set play turn is are was about "
                 "from like um uh so because then into than that which who what when where how if as by "
                 "me i could would can will please also plus minus".split()) | set(
    "et ou mais de du des le la les un une à au aux pour avec dans sur en mon ma mes ton ta tes son sa ses "
    "je tu il elle on nous vous que qui quoi quel quelle est mets met joue allume éteins euh ben donc "
    "parce comme si sans chez vers par".split())


def looks_complete(text):
    t = (text or "").strip()
    if not t or t.endswith((",", "...", "-", "…")):
        return False
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", t.lower())
    return bool(words) and words[-1] not in INCOMPLETE


class VoiceTurn:
    def __init__(self, mgr, session, wake):
        self.mgr = mgr
        self.app = mgr.app
        self.session = session
        self.wake = wake
        self.source = wake.get("source", "kws")
        self.wake_id = wake.get("wake_id")
        self.score = float(wake.get("score", 1.0)) + min(max(float(wake.get("snr_db", 0)) / 30, 0), 1) * 0.3
        self.preroll_bytes = int(wake.get("preroll_ms", 0)) * BYTES_PER_MS
        self.audio = bytearray()
        self.cond = threading.Condition()
        self.vad = Vad(near_field=session.kind in ("phone", "desktop"))
        self.eos = None
        self.audio_ended = False
        self.cancel = threading.Event()
        self.status = "listening"
        self.trace = Trace("voice")
        self.trace.event("wake", **{k: wake.get(k) for k in ("source", "keyword", "score", "doa", "snr_db",
                                                              "preroll_ms", "barge_in", "playing")})
        self.trace.mark("wake")
        self.verify = None
        self.out = None
        self.transcript = ""
        self.reply = ""
        self.route = ""
        self._stt_cache = (0, "")

    # ------------------------------------------------------------ input
    def feed(self, pcm):
        with self.cond:
            before = len(self.audio)
            self.audio += pcm
            if len(self.audio) > self.preroll_bytes:
                self.vad.push(bytes(self.audio[max(before, self.preroll_bytes):]))
            self.cond.notify_all()

    def signal(self, eos=None, ended=False):
        with self.cond:
            if eos:
                self.eos = eos
            if ended:
                self.audio_ended = True
            self.cond.notify_all()

    def live(self):
        with self.cond:
            return bytes(self.audio[self.preroll_bytes:])

    def abort(self, reason):
        if self.cancel.is_set() or self.status == "done":
            return
        self.cancel.set()
        self.status = reason
        if self.out:
            self.out.abort()
        with self.cond:
            self.cond.notify_all()

    def reject(self, reason):
        self.abort(reason)
        msg = {"type": "cancel", "wake_id": self.wake_id, "reason": reason}
        if reason == "rejected" and self.verify:
            msg["verify_score"] = self.verify.get("score")
        self.session.send(msg)
        self.trace.event("cancel", reason=reason)
        self._save()
        if reason in ("empty", "no_speech") and self.mgr.miss(self.session):
            self.mgr.give_up(self.session, self.trace)

    # ------------------------------------------------------------ pipeline
    def run(self):
        try:
            self._run()
        except Exception as e:
            log.exception("turn failed")
            self.trace.event("error", where="turn", error=str(e)[:200])
            self.session.send({"type": "earcon", "name": "error"})
            self.session.send({"type": "session_end", "follow_up": False})
            self.status = "error"
            self._save()
            if self.mgr.miss(self.session):
                self.mgr.give_up(self.session, self.trace)
        finally:
            self.mgr.finished(self)

    def _stt(self, pcm):
        if self.app.settings["enhance"].get("denoise"):
            pcm = denoise(pcm)
        return self.app.stt.transcribe(pcm, language=None)

    def _run(self):
        app, tr = self.app, self.trace
        st = app.settings
        ep = st["endpoint"]
        # pre-roll first
        t_end = time.time() + 1.5
        with self.cond:
            while len(self.audio) < self.preroll_bytes and time.time() < t_end and not self.cancel.is_set():
                self.cond.wait(0.05)
        if self.cancel.is_set():
            return
        if self.preroll_bytes:
            tr.mark("preroll_received")
        if self.source == "kws" and st["wake"].get("verify", True):
            pre = bytes(self.audio[:self.preroll_bytes])
            threading.Thread(target=self._verify, args=(pre,), daemon=True).start()

        # endpointing
        last_partial = time.time()
        partial_busy = threading.Event()
        eot = None
        t_start = time.time()
        while not eot:
            with self.cond:
                self.cond.wait(0.04)
            if self.cancel.is_set():
                return
            if self.verify is not None and self.verify["accepted"] is False:
                return self.reject("rejected")
            v = self.vad
            live_ms = (len(self.audio) - self.preroll_bytes) / BYTES_PER_MS
            speech = v.speech_ms >= 120
            if self.eos == "no_speech" and not speech:
                return self.reject("no_speech")
            if ep.get("partials") and speech and v.silence_ms < 300 and not partial_busy.is_set() and \
                    time.time() - last_partial > ep.get("partial_interval_ms", 900) / 1000:
                last_partial = time.time()
                partial_busy.set()
                threading.Thread(target=self._partial, args=(partial_busy,), daemon=True).start()
            if self.audio_ended or self.eos == "max":
                eot = "device_" + (self.eos or "end")
            elif speech and (v.silence_ms >= ep.get("fast_silence_ms", 800) or self.eos == "silence"):
                # the device's end of speech is only a hint: the words must read
                # complete, otherwise wait for a longer silence
                if ep.get("semantic", True):
                    live = self.live()
                    if self._stt_cache[0] != len(live):
                        t0 = time.time()
                        self._stt_cache = (len(live), self._stt(live))
                        tr.event("semantic_check", text=self._stt_cache[1], ms=round((time.time() - t0) * 1000))
                    if looks_complete(self._stt_cache[1]) and v.silence_ms >= ep.get("fast_silence_ms", 800):
                        eot = "semantic"
                    elif v.silence_ms >= ep.get("slow_silence_ms", 1600):
                        eot = "silence"
                else:
                    eot = "silence"
            elif live_ms > ep.get("max_turn_s", 20) * 1000:
                eot = "max"
            elif not speech and live_ms > 9000:
                return self.reject("no_speech")
            elif time.time() - t_start > ep.get("max_turn_s", 20) + 5:
                # the audio stopped coming (device gone quiet, network): wall clock
                if not speech:
                    return self.reject("no_speech")
                eot = "timeout"
        self.session.send({"type": "eot", "wake_id": self.wake_id})
        tr.mark("eot")
        tr.event("eot", reason=eot, speech_ms=self.vad.speech_ms)
        self.status = "thinking"
        app.bus.publish("turn", id=tr.id, device=self.session.name, status="thinking")

        # final transcript
        live = self.live()
        tr.start("asr")
        if self.source != "kws" and self._stt_cache[0] == len(live):
            text = self._stt_cache[1]
        elif self.source == "kws":
            # the command on its own: even a fragment of the wake word (often
            # misheard) biases the recognizer, e.g. toward English
            text = self._stt(bytes(self.audio[self.preroll_bytes:]))
        else:
            text = self._stt(bytes(self.audio))
        tr.end("asr", chars=len(text))
        tr.mark("asr_done")
        if self.source == "kws":
            # wait for the verdict on the wake word
            t_end = time.time() + 4
            while self.verify is None and time.time() < t_end and not self.cancel.is_set():
                time.sleep(0.02)
            if self.verify is not None and self.verify["accepted"] is False:
                return self.reject("rejected")
            if self.verify is not None and self.verify.get("undecided"):
                full = self._stt(bytes(self.audio))         # wake word in context
                ok, score, phrase, end = app.verifier.check(" ".join(full.split()[:6]), self.session.wake_words)
                strong = float(self.wake.get("score", 0)) >= 0.55
                tr.event("verify_final", accepted=ok or strong, score=score, by="transcript" if ok else
                         "detector" if strong else "none")
                if not ok and not strong:
                    return self.reject("rejected")
            text = app.verifier.strip_wake(text, self.session.wake_words)
        self.transcript = text
        tr.event("transcript", text=text)
        if not text.strip():
            return self.reject("empty")
        self.session.send({"type": "transcript", "text": text, "final": True})
        app.bus.publish("turn", id=tr.id, device=self.session.name, status="thinking", transcript=text)

        # dialog + speech
        rec = app.store.device(self.session.id) or {}
        ctx = TurnContext(app, channel="browser" if self.session.kind == "browser" else "voice",
                          device_id=self.session.id, user=rec.get("user") or "household", trace=tr,
                          cancel=self.cancel)
        self.out = SpeechOut(app, self.session, tr, self.cancel, lang_of=lambda: ctx.lang)
        parts, info = [], {}
        lost = False                # the answer is "I didn't understand"
        gen = app.orchestrator.handle(ctx, text)
        for kind, val in gen:
            if self.cancel.is_set():
                break
            if kind == "sentence":
                if not parts and (ctx.misunderstood or NOT_UNDERSTOOD.search(val)):
                    lost = True
                    if self.mgr.misses_before(self.session) + 1 >= self.mgr.max_misses():
                        # enough tries: say so and stop, instead of asking again
                        gen.close()
                        parts = [say("giveup", ctx.lang)]
                        self.out.say(parts[0])
                        info = {"text": parts[0], "route": "give_up", "follow_up": False}
                        tr.event("give_up", misses=self.mgr.max_misses())
                        break
                parts.append(val)
                self.out.say(val)
                self.status = "speaking"
            elif kind == "done":
                info = val
        if lost:
            self.mgr.miss(self.session, final=info.get("route") == "give_up")
            tr.event("misunderstood", count=self.mgr.misses_before(self.session), max=self.mgr.max_misses())
        else:
            self.mgr.ok(self.session)
        if self.cancel.is_set():
            self.out.abort()
            self._save()
            return
        self.out.finish()
        self.reply = " ".join(parts)
        self.route = info.get("route", "")
        follow = bool(info.get("follow_up")) and st["assistant"].get("follow_up", True) and not ctx.silent
        if ctx.ack and not self.reply:
            self.session.send({"type": "earcon", "name": ctx.ack})
            self.session.send({"type": "led", "pattern": "success"})
            tr.event("ack", earcon=ctx.ack)
        if self.reply:
            self.session.send({"type": "reply", "text": self.reply})
        self.session.send({"type": "session_end", "follow_up": follow,
                           "follow_up_ms": st["assistant"].get("follow_up_ms", 6000)})
        tr.mark("done")
        self.status = "done"
        self._save()

    def _verify(self, pre):
        t0 = time.time()
        try:
            res = self.app.verifier.verify(pre, self.session.wake_words)
            if not res["accepted"]:
                # A 1.5 s clip with just the wake word is hard to recognize
                # ("Mm", "Dina" for "dis lapin"). Reject now only clear other
                # speech; otherwise keep listening and decide on the full
                # request, where the wake phrase is heard in context.
                letters = len(re.sub(r"[^a-zàâçéèêëîïôûùüÿœ]", "", res["transcript"].lower()))
                # lenient over music (its echo garbles the clip), not over the
                # assistant's own answer: that residue is the likeliest false wake
                if self.wake.get("playing") and not self.wake.get("tts") and \
                        (res["score"] >= 0.45 or letters <= 6):
                    res["accepted"] = True
                    res["lenient"] = "playback"
                elif res["score"] >= 0.35 or letters < 10:
                    res["accepted"] = None
                    res["undecided"] = True
        except Exception as e:
            log.warning("verifier failed (%s): accepting the wake", e)
            res = {"accepted": True, "score": None, "error": str(e)[:200], "transcript": ""}
        res["ms"] = round((time.time() - t0) * 1000)
        self.trace.event("verify", **res)
        self.trace.mark("verified")
        self.verify = res
        with self.cond:
            self.cond.notify_all()

    def _partial(self, busy):
        try:
            live = self.live()
            text = self._stt(live)
            self._stt_cache = (len(live), text)
            if text and not self.cancel.is_set():
                self.session.send({"type": "transcript", "text": text, "final": False})
                self.app.bus.publish("turn", id=self.trace.id, device=self.session.name, status="listening",
                                     partial=text)
        except Exception as e:
            log.debug("partial failed: %s", e)
        finally:
            busy.clear()

    def _save(self):
        app = self.app
        audio = ""
        if app.settings["privacy"].get("store_audio") and self.audio:
            d = os.path.join(app.data_dir, "audio")
            os.makedirs(d, exist_ok=True)
            audio = os.path.join(d, self.trace.id + ".wav")
            with open(audio, "wb") as f:
                f.write(wav_bytes(bytes(self.audio)))
        keep = app.settings["privacy"].get("store_transcripts", True)
        turn = {"id": self.trace.id, "ts": self.trace.t0, "device_id": self.session.id, "channel": "voice",
                "user": "", "transcript": self.transcript if keep else "", "reply": self.reply if keep else "",
                "route": self.route, "status": self.status, "trace": self.trace.to_dict(), "audio": audio}
        app.store.save_turn(turn)
        app.bus.publish("turn", id=self.trace.id, device=self.session.name, status=self.status,
                        transcript=turn["transcript"], reply=turn["reply"], route=self.route,
                        trace=turn["trace"])


NOT_UNDERSTOOD = re.compile(
    r"(pas (bien |très bien |tout )?compris|je ne comprends pas|ne suis pas (sûre?|certaine?) de (bien )?comprendre|"
    r"pas (sûre?|certaine?) d'avoir (bien )?compris|(peux|pourrais|pouvez|pourriez)[- ](tu|vous) répéter|"
    r"tu peux répéter|répéter (s'il|stp|svp|ta question|votre question|ce que)|didn't (quite )?(catch|understand|get) (that|it|you)|"
    r"not sure (what|i understand)|don't understand|(could|can) you (say|repeat)|say (that|it) again)", re.I)


class TurnManager:
    MISS_WINDOW_S = 90

    def __init__(self, app):
        self.app = app
        self.misses = {}            # device id -> (consecutive misunderstood turns, time of the last)
        self.text_locks = {}
        self.lock = threading.Lock()
        self.active = {}            # device id -> VoiceTurn
        self.window = []
        self.timer = None

    def on_wake(self, session, msg):
        old = self.active.get(session.id)
        if old:
            old.abort("barge_in")
            old.trace.event("cancel", reason="barge_in")
            old._save()
        turn = VoiceTurn(self, session, msg)
        with self.lock:
            self.active[session.id] = turn
        self.app.bus.publish("turn", id=turn.trace.id, device=session.name, status="listening",
                             source=turn.source)
        arb = self.app.settings["wake"].get("arbitration_ms", 180)
        others = [s for s in self.app.devices.online("satellite") if s.id != session.id]
        if turn.source == "kws" and arb > 0 and others:
            with self.lock:
                self.window.append(turn)
                if self.timer is None:
                    self.timer = threading.Timer(arb / 1000, self._decide)
                    self.timer.start()
        else:
            self._start(turn)

    def _decide(self):
        with self.lock:
            cands, self.window, self.timer = self.window, [], None
        cands = [t for t in cands if not t.cancel.is_set()]
        if not cands:
            return
        best = max(cands, key=lambda t: t.score)
        for t in cands:
            if t is not best:
                t.trace.event("arbitration", winner=best.session.name)
                t.reject("arbitration")
                with self.lock:
                    if self.active.get(t.session.id) is t:
                        del self.active[t.session.id]
        best.trace.event("arbitration", candidates=[t.session.name for t in cands])
        self._start(best)

    def _start(self, turn):
        turn.trace.mark("accepted")
        turn.session.send({"type": "wake_ack", "wake_id": turn.wake_id})
        threading.Thread(target=turn.run, name="turn-%s" % turn.session.id, daemon=True).start()

    # ------------------------------------------------------------ misunderstandings
    def max_misses(self):
        return int(self.app.settings["assistant"].get("max_misses", 3) or 0) or 10 ** 6

    def misses_before(self, session):
        n, t = self.misses.get(session.id, (0, 0))
        return n if time.time() - t < self.MISS_WINDOW_S else 0

    def miss(self, session, final=False):
        """Counts a misunderstood turn; True when it is one too many."""
        n = self.misses_before(session) + 1
        if final or n >= self.max_misses():
            self.misses.pop(session.id, None)
            return not final
        self.misses[session.id] = (n, time.time())
        return False

    def ok(self, session):
        self.misses.pop(session.id, None)

    def give_up(self, session, trace=None):
        """After too many misunderstood tries: says so, and stops listening."""
        rec = self.app.store.device(session.id) or {}
        user = rec.get("user") or "household"
        lang = self.app.orchestrator.last_lang.get(user) or \
            self.app.settings["assistant"].get("default_language", "en")
        if trace:
            trace.event("give_up", misses=self.max_misses())

        def run():
            out = SpeechOut(self.app, session, None, threading.Event(), lang=lang)
            out.say(say("giveup", lang))
            out.finish()
            session.send({"type": "session_end", "follow_up": False})
        threading.Thread(target=run, name="give-up", daemon=True).start()

    # ------------------------------------------------------------ typed requests
    def on_text(self, session, text, speak=True):
        """A typed request on a device (phone keyboard, desktop overlay): the
        answer comes back as text, and spoken if `speak`."""
        text = text.strip()
        if not text:
            return
        old = self.active.get(session.id)
        if old:
            old.abort("barge_in")
        threading.Thread(target=self._text_turn, args=(session, text, speak), name="text-%s" % session.id,
                         daemon=True).start()

    def on_confirm(self, session, yes):
        """The confirmation buttons of a device's UI."""
        conv = self.app.conversations.get("device:%s" % session.id)
        word = {"fr": ("oui", "non")}.get(conv.lang or "", ("yes", "no"))[0 if yes else 1]
        self.on_text(session, word, speak=False)

    def _text_turn(self, session, text, speak):
        with self.lock:
            lk = self.text_locks.setdefault(session.id, threading.Lock())
        with lk:                    # one at a time per device (a "yes" waits for its question)
            self._text_turn_locked(session, text, speak)

    def _text_turn_locked(self, session, text, speak):
        app = self.app
        tr = Trace(session.name)
        tr.event("text", text=text, speak=speak)
        rec = app.store.device(session.id) or {}
        ctx = TurnContext(app, channel="browser" if session.kind == "browser" else "voice",
                          device_id=session.id, user=rec.get("user") or "household", trace=tr)
        out = SpeechOut(app, session, tr, threading.Event(), lang_of=lambda: ctx.lang) if speak else None
        parts, info, status = [], {}, "done"
        try:
            for kind, val in app.orchestrator.handle(ctx, text):
                if kind == "sentence":
                    parts.append(val)
                    if out:
                        out.say(val)
                elif kind == "done":
                    info = val
            if out:
                out.finish()
        except Exception as e:
            log.exception("text turn failed")
            tr.event("error", where="text", error=str(e)[:200])
            status = "error"
        reply = info.get("text") or " ".join(parts)
        if ctx.ack and not reply:
            session.send({"type": "earcon", "name": ctx.ack})
        session.send({"type": "reply", "text": reply, "ack": ctx.ack or ""})
        session.send({"type": "session_end", "follow_up": bool(info.get("follow_up")) and speak,
                      "follow_up_ms": app.settings["assistant"].get("follow_up_ms", 6000)})
        tr.mark("done")
        keep = app.settings["privacy"].get("store_transcripts", True)
        app.store.save_turn({"id": tr.id, "ts": tr.t0, "device_id": session.id, "channel": "text",
                             "user": ctx.user, "transcript": text if keep else "", "reply": reply if keep else "",
                             "route": info.get("route", ""), "status": status, "trace": tr.to_dict()})
        app.bus.publish("turn", id=tr.id, device=session.name, status=status, transcript=text, reply=reply,
                        route=info.get("route", ""))

    def finished(self, turn):
        with self.lock:
            if self.active.get(turn.session.id) is turn:
                del self.active[turn.session.id]

    def on_audio(self, session, pcm):
        t = self.active.get(session.id)
        if t and not t.cancel.is_set():
            t.feed(pcm)

    def on_eos(self, session, msg):
        t = self.active.get(session.id)
        if t:
            t.signal(eos=msg.get("reason"))

    def on_audio_end(self, session, msg):
        t = self.active.get(session.id)
        if t:
            t.signal(ended=True)

    def cancel_device(self, session, reason):
        t = self.active.get(session.id)
        if t and t.status not in ("done", "error"):
            t.abort(reason)
            t.trace.event("cancel", reason=reason)
            t._save()
            self.finished(t)

    def view(self):
        with self.lock:
            return [{"device": t.session.name, "status": t.status, "transcript": t.transcript, "id": t.trace.id}
                    for t in self.active.values()]
