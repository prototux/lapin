"""The assistant client: the protocol state machine shared by the overlay, the
headless mode and the debug CLI.

Lives in the Qt main thread. The link thread hands messages over through a
queued signal; playback audio goes straight to the mixer, microphone frames
straight to the link.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal

from . import i18n
from .audio import SPEECH_KINDS, Mic, Mixer
from .link import ServerLink, make_hello
from .tools import Registry

log = logging.getLogger("core")


class Assistant(QObject):
    state_changed = Signal(str)             # idle | listening | thinking | speaking
    status_changed = Signal(str, str)       # link status, detail
    transcript = Signal(str, bool)          # text, final
    reply = Signal(str, str)                # text, ack
    confirm_requested = Signal(str, str)    # summary, tool
    confirm_cleared = Signal()
    notice = Signal(str)                    # short message for the user
    tool_event = Signal(str, object, object)  # name, args, result
    turn_started = Signal(str)              # wake source, or "text"
    finished = Signal(bool)                 # turn over (follow_up), after the speech drained
    attention = Signal()                    # the server wants the user (listen)
    message = Signal(object)                # every server message (debug CLI)

    _incoming = Signal(object)
    _status_in = Signal(str, str)
    _tool_done = Signal(str, object, object)

    def __init__(self, cfg, audio=True):
        super().__init__()
        self.cfg = cfg
        self.tools = Registry(cfg)
        self.mixer = Mixer(cfg["output_device"], enabled=audio, on_event=self._on_playback)
        self.mixer.set_volume(cfg["volume"])
        self.mic = Mic(self._on_mic_frame, cfg["input_device"])
        self.source = None              # replaces the microphone (debug --wav)
        self.link = ServerLink(self._hello, self._incoming.emit, self.mixer.feed, self._status_in.emit)
        self.state = "idle"
        self.status = "connecting"
        self.status_detail = ""
        self.wake_id = 0
        self.listening = False
        self.turn_seq = 0               # every turn started here (voice or typed)
        self.confirm_turn = None        # turn_seq when a confirm arrived
        self.last_tool = 0.0            # time of the last tool call
        self.follow_up_ok = lambda: True    # the UI says if a follow-up may open the mic
        self._end_gen = 0
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tool")
        self._incoming.connect(self._on_message)
        self._status_in.connect(self._on_status)
        self._tool_done.connect(self.tool_event)

    def start(self):
        self.link.start()

    def shutdown(self):
        self.stop_listening()
        self.link.stop()
        self.mixer.close_output()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def reconnect(self):
        self.link.reconnect()

    @property
    def online(self):
        return self.link.up

    # ------------------------------------------------------------ helpers
    def _hello(self):
        return self.cfg["server_url"].strip(), make_hello(self.cfg, self.tools.specs())

    def _set_state(self, s):
        if s != self.state:
            self.state = s
            self.link.send({"type": "state", "state": s, "muted": bool(self.cfg["mic_muted"])})
            self.state_changed.emit(s)

    def _on_mic_frame(self, pcm):
        # audio thread (or the --wav feeder)
        if self.listening:
            self.link.send_audio(pcm)

    def _on_playback(self, sid, event):
        # audio thread; the optional "playback" report (PROTOCOL.md section 3)
        self.link.send({"type": "playback", "id": sid, "event": event})

    def _not_ready(self):
        if self.link.up:
            return False
        if self.status == "pending":
            msg = i18n.tr("pending")
        elif self.status_detail and self.status in ("offline", "error"):
            msg = "%s (%s)" % (i18n.tr("offline"), self.status_detail)
        else:
            msg = i18n.tr("offline")
        self._error_sound()
        self.notice.emit(msg)
        return True

    def _error_sound(self):
        if self.cfg["earcons"]:
            self.mixer.earcon("error")

    def _clear_confirm(self):
        if self.confirm_turn is not None:
            self.confirm_turn = None
            self.confirm_cleared.emit()

    def _new_turn(self, source):
        self._end_gen += 1          # forget a pending session_end
        self.turn_seq += 1
        if source != "followup":
            self._clear_confirm()   # "until another turn starts"
        self.mixer.stop(SPEECH_KINDS)   # barge-in: the old answer stops now
        self.turn_started.emit(source)

    # ------------------------------------------------------------ actions (UI)
    def start_listening(self, source="button"):
        if self.cfg["mic_muted"]:
            self.notice.emit(i18n.tr("mic_muted"))
            return False
        if self._not_ready():
            return False
        try:
            if self.source:
                self.source.start()
            else:
                self.mic.start()
        except Exception as e:
            log.warning("microphone: %s", e)
            self._error_sound()
            self.notice.emit(i18n.tr("mic_error", e))
            return False
        self._new_turn(source)
        self.wake_id += 1
        self.link.send({"type": "wake", "wake_id": self.wake_id, "source": source, "score": 1, "preroll_ms": 0})
        self.listening = True
        self.mixer.set_mic_open(True)
        if self.cfg["earcons"]:
            self.mixer.earcon("listen", gain=0.5)
        self._set_state("listening")
        return True

    def stop_listening(self):
        was = self.listening
        self.listening = False
        if self.source:
            self.source.stop()
        self.mic.stop()
        self.mixer.set_mic_open(False)
        return was

    def end_turn(self):
        """The user ends the turn by hand (hotkey again, mic button)."""
        if self.listening:
            self.link.send({"type": "audio_end", "wake_id": self.wake_id})
            self.stop_listening()
            self._set_state("thinking")

    def toggle(self):
        """Hotkey / mic button: listen, or end the turn when listening.
        While thinking or speaking, interrupts and listens again (barge-in)."""
        if self.listening:
            self.end_turn()
        else:
            self.start_listening("button")

    def cancel(self):
        """The overlay was dismissed: abort the turn, silence the answer."""
        busy = self.listening or self.state != "idle"
        self.stop_listening()
        self.mixer.stop(SPEECH_KINDS)
        self._end_gen += 1
        if busy:
            self.link.send({"type": "button", "action": "cancel"})
        self._set_state("idle")

    def stop_audio(self):
        self.link.send({"type": "button", "action": "stop"})
        self.mixer.stop()

    def send_text(self, text):
        text = text.strip()
        if not text or self._not_ready():
            return False
        if self.stop_listening():
            self.link.send({"type": "button", "action": "cancel"})
        self._new_turn("text")
        self.link.send({"type": "text", "text": text, "speak": bool(self.cfg["speak_typed"])})
        self._set_state("thinking")
        return True

    def answer_confirm(self, yes):
        self._clear_confirm()
        if self._not_ready():
            return
        self.stop_listening()       # the server drops the voice turn for the answer
        self._end_gen += 1
        self.mixer.stop(SPEECH_KINDS)
        self.turn_seq += 1
        self.link.send({"type": "confirm_reply", "yes": bool(yes)})
        self._set_state("thinking")

    # ------------------------------------------------------------ link events
    def _on_status(self, status, detail):
        self.status, self.status_detail = status, detail
        if status != "online" and (self.listening or self.state != "idle"):
            self.stop_listening()
            self.mixer.stop(SPEECH_KINDS)
            self._clear_confirm()
            self.state = "idle"
            self.state_changed.emit("idle")
        self.status_changed.emit(status, detail)

    def _on_message(self, m):
        self.message.emit(m)
        t = m.get("type")
        if t == "eot":
            if m.get("wake_id", self.wake_id) == self.wake_id and self.listening:
                self.stop_listening()
                self._set_state("thinking")
        elif t == "cancel":
            if m.get("wake_id", self.wake_id) != self.wake_id or m.get("reason") == "barge_in":
                return              # an older turn we interrupted ourselves
            self.stop_listening()
            self._set_state("idle")
            reason = m.get("reason", "")
            if reason in ("no_speech", "empty"):
                self.notice.emit(i18n.tr("no_speech"))
            elif reason not in ("button", "disconnected"):
                self.notice.emit(i18n.tr("cancelled"))
            self.finished.emit(False)
        elif t == "transcript":
            self.transcript.emit(m.get("text", ""), bool(m.get("final")))
        elif t == "reply":
            self.reply.emit(m.get("text", ""), m.get("ack", "") or "")
        elif t == "stream_open":
            self.mixer.open(m["id"], m.get("kind", "tts"), m.get("rate", 24000), m.get("channels", 1),
                            m.get("gain_db", 0), m.get("prebuffer_ms"))
            if m.get("kind", "tts") in SPEECH_KINDS and not self.listening:
                self._set_state("speaking")
        elif t == "stream_close":
            self.mixer.close(m.get("id"), bool(m.get("drain", True)))
        elif t == "stream_pause":
            self.mixer.pause(m.get("id"), bool(m.get("paused")))
        elif t == "session_end":
            self._end_gen += 1
            self._after_drain(self._end_gen, bool(m.get("follow_up")), time.monotonic())
        elif t == "earcon":
            if self.cfg["earcons"]:
                self.mixer.earcon(m.get("name", "notify"))
        elif t == "stop":
            self.mixer.stop()
            if self.state == "speaking":
                self._set_state("idle")
        elif t == "listen":
            self.attention.emit()
            if not self.listening:
                self.start_listening("followup")
        elif t == "set":
            if "volume" in m:
                try:
                    v = max(0, min(100, int(m["volume"])))
                    self.mixer.set_volume(v)
                    self.cfg.update({"volume": v})
                except (TypeError, ValueError):
                    pass
            if "mic_muted" in m:
                self.cfg.update({"mic_muted": bool(m["mic_muted"])})
                if m["mic_muted"] and self.listening:
                    self.cancel()
        elif t == "confirm":
            self.confirm_turn = self.turn_seq
            self.confirm_requested.emit(m.get("summary", ""), m.get("tool", ""))
        elif t == "tool_call":
            self.last_tool = time.monotonic()
            self.pool.submit(self._run_tool, m)
        elif t == "error":
            self.notice.emit(m.get("message", i18n.tr("error")))

    def _after_drain(self, gen, follow_up, t0):
        """session_end: let the speech play out, then follow up or go idle."""
        if gen != self._end_gen:
            return                  # a new turn started meanwhile
        if self.mixer.busy() and time.monotonic() - t0 < 120:
            QTimer.singleShot(60, lambda: self._after_drain(gen, follow_up, t0))
            return
        if self.confirm_turn is not None and self.turn_seq > self.confirm_turn:
            self._clear_confirm()   # the turn after the question is over
        if follow_up and self.follow_up_ok() and self.start_listening("followup"):
            return
        self._set_state("idle")
        self.finished.emit(follow_up)

    def _run_tool(self, m):
        name, args = m.get("name", ""), m.get("args") or {}
        log.info("tool call %s %s", name, args)
        res = self.tools.call(name, args)
        log.info("tool result %s", res)
        self.link.send({"type": "tool_result", "call_id": m.get("call_id"), "result": res})
        self._tool_done.emit(name, args, res)
