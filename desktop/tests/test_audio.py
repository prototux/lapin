import unittest

import numpy as np

from lapin_desktop.audio import MIC_FRAME, Mic, Mixer, Resampler, db_to_gain, make_earcon


def tone(rate, freq, seconds, amp=0.5, channels=1):
    t = np.arange(int(rate * seconds)) / rate
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], channels, axis=1)


def pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


class ResamplerTest(unittest.TestCase):
    def test_keeps_frequency_and_length_across_blocks(self):
        for src, dst in [(24000, 16000), (48000, 16000), (44100, 16000), (24000, 48000), (22050, 48000)]:
            x = tone(src, 1000, 1.0)
            r = Resampler(src, dst, 1)
            y = np.concatenate([r.process(x[i:i + 701]) for i in range(0, len(x), 701)])
            self.assertLess(abs(len(y) - dst), 3, (src, dst))
            spec = np.abs(np.fft.rfft(y[1000:1000 + dst // 2, 0]))
            self.assertEqual(int(np.argmax(spec)) * 2, 1000, (src, dst))

    def test_downsampling_filters_aliases(self):
        # 7 kHz at 48 kHz would fold to 9 kHz -> 7 kHz... use 12 kHz: above 8 kHz Nyquist of 16 kHz
        x = tone(48000, 12000, 0.5)
        y = Resampler(48000, 16000, 1).process(x)
        self.assertLess(float(np.abs(y[200:]).max()), 0.1)


class MicTest(unittest.TestCase):
    def test_frames_are_20ms(self):
        frames = []
        m = Mic(frames.append)
        m.feed(np.zeros(1000, dtype=np.float32))
        m.feed(np.zeros(300, dtype=np.float32))
        self.assertEqual(len(frames), 4)
        self.assertTrue(all(len(f) == 2 * MIC_FRAME for f in frames))


class MixerTest(unittest.TestCase):
    def mixer(self):
        events = []
        m = Mixer(enabled=False, on_event=lambda sid, ev: events.append((sid, ev)))
        m.out = True            # pretend an output is open: buffers fill, render() plays
        m.out_rate = 48000
        m.set_volume(100)
        return m, events

    def test_stream_plays_and_drains(self):
        m, events = self.mixer()
        m.open(7, "tts", 24000, 1)
        m.feed(7, pcm(tone(24000, 440, 0.5)[:, 0]))
        m.close(7, drain=True)
        self.assertTrue(m.busy())
        out = np.concatenate([m.render(480) for _ in range(60)])
        self.assertFalse(m.busy())
        self.assertEqual(events, [(7, "started"), (7, "finished")])
        self.assertGreater(float(np.abs(out).max()), 0.3)

    def test_prebuffer_waits(self):
        m, events = self.mixer()
        m.open(1, "tts", 24000, 1)
        m.feed(1, pcm(tone(24000, 440, 0.05)[:, 0]))     # 50 ms < 150 ms prebuffer
        self.assertEqual(float(np.abs(m.render(480)).max()), 0.0)
        self.assertEqual(events, [])

    def test_media_ducked_under_speech_and_mic(self):
        m, _ = self.mixer()
        m.open(1, "media", 48000, 2)
        m.feed(1, pcm(tone(48000, 300, 3, channels=2).reshape(-1)))
        loud = np.abs(np.concatenate([m.render(480) for _ in range(40)])).max()
        m.open(2, "tts", 24000, 1)
        m.feed(2, pcm(tone(24000, 3000, 2, amp=0.0)[:, 0]))     # silent speech: only the duck shows
        for _ in range(40):
            m.render(480)
        ducked = np.abs(m.render(480)).max()
        self.assertAlmostEqual(ducked / loud, db_to_gain(-18), delta=0.02)
        m.stop(("tts",))
        m.set_mic_open(True)
        for _ in range(40):
            m.render(480)
        self.assertAlmostEqual(np.abs(m.render(480)).max() / loud, db_to_gain(-40), delta=0.005)

    def test_barge_in_stops_speech_now(self):
        m, events = self.mixer()
        m.open(3, "tts", 24000, 1)
        m.feed(3, pcm(tone(24000, 440, 2)[:, 0]))
        m.render(480)
        m.stop(("tts",))
        self.assertFalse(m.busy())
        self.assertIn((3, "finished"), events)
        self.assertEqual(float(np.abs(m.render(480)).max()), 0.0)

    def test_disabled_mixer_finishes_on_close(self):
        events = []
        m = Mixer(enabled=False, on_event=lambda sid, ev: events.append((sid, ev)))
        m.open(5, "tts", 24000, 1)
        m.feed(5, b"\x00\x01" * 2400)
        m.close(5, drain=True)
        self.assertFalse(m.busy())
        self.assertEqual(m.stats[5], 4800)
        self.assertEqual(events, [(5, "finished")])

    def test_volume(self):
        m, _ = self.mixer()
        m.set_volume(50)
        m.open(1, "tts", 48000, 1)
        m.feed(1, pcm(tone(48000, 440, 0.5)[:, 0]))
        m.close(1)
        peak = np.abs(np.concatenate([m.render(480) for _ in range(30)])).max()
        self.assertAlmostEqual(peak, 0.5 * 0.25, delta=0.02)


class EarconTest(unittest.TestCase):
    def test_earcons(self):
        for name in ("done", "error", "listen", "listen_end", "notify", "whatever"):
            x = make_earcon(name, 48000)
            self.assertGreater(len(x), 48000 * 0.1, name)
            self.assertLess(len(x), 48000 * 1.0, name)
            self.assertLessEqual(float(np.abs(x).max()), 1.0, name)
            self.assertLess(abs(float(x[-1])), 0.01, name)     # no click at the end


if __name__ == "__main__":
    unittest.main()
