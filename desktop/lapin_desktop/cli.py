"""Debug/testing commands: --text and --wav run one turn against the server
and print what happens (transcript, reply, tool calls, confirmations)."""

import json
import logging
import sys
import threading
import time
import wave

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .audio import MIC_FRAME, MIC_RATE, Resampler
from .core import Assistant

log = logging.getLogger("cli")


def out(tag, text):
    t = time.time()
    print("%s.%03d [%s] %s" % (time.strftime("%H:%M:%S", time.localtime(t)), t % 1 * 1000, tag, text), flush=True)


class WavSource:
    """Feeds a WAV file as the microphone of a voice turn, in real time, then
    silence until the server ends the turn (or audio_end after tail_s)."""

    def __init__(self, path, assistant, tail_s=4.0):
        self.assistant = assistant
        self.tail_s = tail_s
        with wave.open(path, "rb") as w:
            ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError("only 16-bit WAV files are supported")
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, ch).mean(axis=1, keepdims=True) / 32768
        x = Resampler(rate, MIC_RATE, 1).process(x)[:, 0]
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        self.frames = [pcm[i:i + 2 * MIC_FRAME] for i in range(0, len(pcm) - 2 * MIC_FRAME + 1, 2 * MIC_FRAME)]
        self.duration = len(x) / MIC_RATE
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        self.stopped.clear()
        self.thread = threading.Thread(target=self._run, name="wav", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def _run(self):
        a = self.assistant
        t_end = time.monotonic() + 2
        while not a.listening and time.monotonic() < t_end and not self.stopped.is_set():
            time.sleep(0.005)
        silence = bytes(2 * MIC_FRAME)
        n_tail = int(self.tail_s * MIC_RATE / MIC_FRAME)
        t0 = time.monotonic()
        for i, frame in enumerate(self.frames + [silence] * n_tail):
            if self.stopped.is_set():
                return
            a._on_mic_frame(frame)
            delay = t0 + (i + 1) * MIC_FRAME / MIC_RATE - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        if not self.stopped.is_set():
            out("wav", "no end of turn from the server: sending audio_end")
            a.link.send({"type": "audio_end", "wake_id": a.wake_id})


def run(args, cfg):
    app = QApplication.instance()
    a = Assistant(cfg, audio=not args.no_audio)
    a.follow_up_ok = lambda: False      # one turn only
    if args.speak is not None:
        cfg.data["speak_typed"] = args.speak == "yes"   # not saved
    state = {"started": False, "code": 1}

    def finish(code):
        if state.get("over"):
            return
        state["over"] = True
        state["code"] = code
        for sid, n in sorted(a.mixer.stats.items()):
            out("audio", "stream %s: %d bytes received" % (sid, n))
        a.shutdown()
        QTimer.singleShot(100, app.quit)

    def begin():
        if state["started"]:
            return
        state["started"] = True
        if args.wav:
            src = WavSource(args.wav, a)
            a.source = src
            out("wav", "%s: %.1f s" % (args.wav, src.duration))
            if not a.start_listening("button"):
                finish(1)
        else:
            out("text", args.text)
            if not a.send_text(args.text):
                finish(1)

    def on_status(status, detail):
        out("link", "%s %s" % (status, detail))
        if status == "pending":
            out("link", "approve device %s on the server's Devices page" % cfg["device_id"])
        elif status == "online":
            QTimer.singleShot(200, begin)
        elif status == "error":
            finish(2)

    def on_message(m):
        t = m.get("type")
        if t in ("transcript", "reply", "cancel", "session_end", "eot", "earcon", "stream_open", "stream_close",
                 "confirm", "tool_call", "error", "listen", "set", "stop"):
            out("recv", json.dumps(m, ensure_ascii=False))

    def on_confirm(summary, tool):
        out("confirm", "%s (%s): will answer %s once the question is over" % (summary, tool, args.confirm))
        state["confirm"] = True

    def on_finished(follow_up):
        out("done", "turn over (follow_up=%s)" % follow_up)
        if state.pop("confirm", False):
            # tap Yes/No like the overlay would
            out("send", json.dumps({"type": "confirm_reply", "yes": args.confirm == "yes"}))
            a.answer_confirm(args.confirm == "yes")
            return
        finish(0)

    a.status_changed.connect(on_status)
    a.message.connect(on_message)
    a.confirm_requested.connect(on_confirm)
    a.tool_event.connect(lambda n, ar, r: out("tool", "%s %s -> %s" % (n, json.dumps(ar, ensure_ascii=False),
                                                                        json.dumps(r, ensure_ascii=False))))
    a.notice.connect(lambda s: out("notice", s))
    a.finished.connect(on_finished)
    QTimer.singleShot(int(args.timeout * 1000), lambda: (out("error", "timeout"), finish(3)))
    out("device", "%s (%s), server %s" % (cfg["device_id"], cfg.name(), cfg["server_url"]))
    a.start()
    app.exec()
    return state["code"]


if __name__ == "__main__":
    sys.exit("use: python -m lapin_desktop --text ... | --wav ...")
