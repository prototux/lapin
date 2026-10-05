"""The protocol state machine, with a fake link (no network, no microphone)."""

import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LAPIN_DRY_RUN"] = "1"

from PySide6.QtWidgets import QApplication  # noqa: E402

from lapin_desktop import i18n  # noqa: E402
from lapin_desktop.config import Config  # noqa: E402
from lapin_desktop.core import Assistant  # noqa: E402


class FakeLink:
    def __init__(self):
        self.up = True
        self.sent = []
        self.audio = 0

    def send(self, m):
        self.sent.append(m)
        return True

    def send_audio(self, pcm):
        self.audio += 1
        return True

    def types(self):
        return [m["type"] for m in self.sent if m["type"] not in ("state", "playback")]

    def stop(self):
        pass

    def reconnect(self):
        pass


class FakeSource:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class CoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        i18n.setup("en")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(self.tmp.name)
        self.cfg.update({"earcons": False})
        self.a = Assistant(self.cfg, audio=False)
        self.link = self.a.link = FakeLink()
        self.a.source = FakeSource()
        self.events = []
        for name in ("state_changed", "transcript", "reply", "confirm_requested", "confirm_cleared", "notice",
                     "finished", "tool_event"):
            getattr(self.a, name).connect(lambda *args, n=name: self.events.append((n,) + args))

    def tearDown(self):
        self.a.pool.shutdown(wait=True)
        self.tmp.cleanup()

    def msg(self, **m):
        self.a._on_message(m)

    def spin(self, seconds=0.3):
        t = time.monotonic() + seconds
        while time.monotonic() < t:
            self.app.processEvents()
            time.sleep(0.01)

    def test_voice_turn(self):
        self.assertTrue(self.a.start_listening())
        wake = [m for m in self.link.sent if m["type"] == "wake"][0]
        self.assertEqual((wake["wake_id"], wake["source"]), (1, "button"))
        self.a._on_mic_frame(b"\0" * 640)
        self.assertEqual(self.link.audio, 1)
        self.msg(type="eot", wake_id=1)
        self.assertFalse(self.a.listening)
        self.a._on_mic_frame(b"\0" * 640)
        self.assertEqual(self.link.audio, 1)        # nothing sent after eot
        self.assertEqual(self.a.state, "thinking")
        self.msg(type="transcript", text="hello", final=True)
        self.msg(type="stream_open", id=4, kind="tts", rate=24000, channels=1)
        self.assertEqual(self.a.state, "speaking")
        self.msg(type="reply", text="Hi!")
        self.msg(type="stream_close", id=4, drain=True)
        self.msg(type="session_end", follow_up=False)
        self.spin()
        self.assertEqual(self.a.state, "idle")
        self.assertIn(("finished", False), self.events)
        self.assertIn(("reply", "Hi!", ""), self.events)

    def test_follow_up_reopens_mic(self):
        self.a.start_listening()
        self.msg(type="eot", wake_id=1)
        self.msg(type="session_end", follow_up=True)
        self.spin()
        wakes = [m for m in self.link.sent if m["type"] == "wake"]
        self.assertEqual([(w["wake_id"], w["source"]) for w in wakes], [(1, "button"), (2, "followup")])
        self.assertTrue(self.a.listening)

    def test_no_follow_up_when_hidden(self):
        self.a.follow_up_ok = lambda: False
        self.a.start_listening()
        self.msg(type="session_end", follow_up=True)
        self.spin()
        self.assertEqual(len([m for m in self.link.sent if m["type"] == "wake"]), 1)
        self.assertEqual(self.a.state, "idle")

    def test_cancel_of_old_turn_ignored(self):
        self.a.start_listening()
        self.a.start_listening()            # barge-in: wake 2
        self.msg(type="cancel", wake_id=1, reason="barge_in")
        self.assertTrue(self.a.listening)
        self.msg(type="cancel", wake_id=2, reason="no_speech")
        self.assertFalse(self.a.listening)
        self.assertIn(("notice", "I didn't hear anything."), self.events)

    def test_barge_in_stops_speech(self):
        self.msg(type="stream_open", id=9, kind="tts", rate=24000, channels=1)
        self.assertTrue(self.a.mixer.busy())
        self.a.start_listening()
        self.assertFalse(self.a.mixer.busy())

    def test_end_turn_by_hand(self):
        self.a.toggle()
        self.a.toggle()
        self.assertEqual(self.link.types(), ["wake", "audio_end"])
        self.assertEqual(self.a.state, "thinking")

    def test_dismiss_sends_cancel(self):
        self.a.start_listening()
        self.a.cancel()
        self.assertIn({"type": "button", "action": "cancel"}, self.link.sent)
        self.assertEqual(self.a.state, "idle")
        n = len(self.link.sent)
        self.a.cancel()                     # idle: nothing to cancel
        self.assertEqual(len([m for m in self.link.sent[n:] if m["type"] == "button"]), 0)

    def test_text_turn_cancels_listening(self):
        self.a.start_listening()
        self.a.send_text("Quelle heure est-il ?")
        self.assertEqual(self.link.types(), ["wake", "button", "text"])
        text = [m for m in self.link.sent if m["type"] == "text"][0]
        self.assertEqual(text["speak"], False)     # typed: text-only answer by default
        self.assertFalse(self.a.listening)

    def test_confirm_flow(self):
        self.a.send_text("shut down")
        self.msg(type="confirm", summary="shutdown the computer", tool="power")
        self.assertIn(("confirm_requested", "shutdown the computer", "power"), self.events)
        self.msg(type="reply", text="Shut down, right?")
        self.msg(type="session_end", follow_up=True)
        self.spin()
        self.assertTrue(self.a.listening)               # follow-up turn
        self.assertNotIn(("confirm_cleared",), self.events)     # buttons stay for the follow-up
        self.a.answer_confirm(False)
        self.assertFalse(self.a.listening)
        self.assertEqual(self.link.types()[-1], "confirm_reply")
        reply = [m for m in self.link.sent if m["type"] == "confirm_reply"][0]
        self.assertEqual(reply["yes"], False)
        self.assertIn(("confirm_cleared",), self.events)

    def test_confirm_cleared_after_next_turn(self):
        self.a.send_text("shut down")
        self.msg(type="confirm", summary="x", tool="power")
        self.msg(type="session_end", follow_up=False)
        self.spin()
        self.assertNotIn(("confirm_cleared",), self.events)
        self.a.start_listening()            # a new (button) turn starts
        self.assertIn(("confirm_cleared",), self.events)

    def test_tool_call(self):
        self.msg(type="tool_call", call_id="c7", name="open_folder", args={"name": "downloads"})
        t = time.monotonic() + 5
        while time.monotonic() < t and not any(m["type"] == "tool_result" for m in self.link.sent):
            time.sleep(0.02)
        res = [m for m in self.link.sent if m["type"] == "tool_result"][0]
        self.assertEqual(res["call_id"], "c7")
        self.assertTrue(res["result"]["ok"])
        self.msg(type="tool_call", call_id="c8", name="nope", args={})
        t = time.monotonic() + 5
        while time.monotonic() < t and len([m for m in self.link.sent if m["type"] == "tool_result"]) < 2:
            time.sleep(0.02)
        res = [m for m in self.link.sent if m["type"] == "tool_result"][1]
        self.assertIn("error", res["result"])

    def test_set_volume_and_mute(self):
        self.msg(type="set", volume=40)
        self.assertEqual(self.cfg["volume"], 40)
        self.assertAlmostEqual(self.a.mixer.volume, 0.16)
        self.msg(type="set", mic_muted=True)
        self.assertFalse(self.a.start_listening())
        self.assertIn(("notice", "The microphone is muted."), self.events)

    def test_offline(self):
        self.link.up = False
        self.assertFalse(self.a.start_listening())
        self.assertFalse(self.a.send_text("hi"))
        self.assertTrue(any(e[0] == "notice" for e in self.events))

    def test_hello(self):
        url, hello = self.a._hello()
        self.assertEqual(hello["kind"], "desktop")
        self.assertTrue(hello["capabilities"]["confirm_ui"])
        self.assertTrue(hello["device_id"].startswith("desktop-"))
        self.assertEqual({t["name"] for t in hello["tools"]} >= {"open_app", "power", "screenshot"}, True)


if __name__ == "__main__":
    unittest.main()
