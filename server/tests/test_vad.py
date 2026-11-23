"""The near-field VAD (phones, computers): the user's level and the background."""

import unittest

import numpy as np

from assistant.audio import Vad

RATE = 16000


def noise(ms, db, seed=0):
    """Broadband noise at a level in dBFS (RMS)."""
    x = np.random.default_rng(seed).normal(0, 1, RATE * ms // 1000)
    return x * 32767 * 10 ** (db / 20)


def voice(ms, db, seed=1):
    """Speech-like: 150 ms syllables, 50 ms gaps."""
    x = noise(ms, db, seed)
    t = np.arange(len(x)) * 1000 // RATE
    return x * ((t % 200) < 150)


def chime(db):
    """Lapin's follow-up earcon: one 110 ms note."""
    t = np.arange(RATE * 110 // 1000) / RATE
    return np.sin(2 * np.pi * 1174.7 * t) * 32767 * 10 ** (db / 20) * np.sqrt(2)


def speech_ms(*parts):
    vad = Vad(near_field=True)
    vad.shape = None        # level only: the WebRTC detector doesn't take synthetic sounds for speech
    sig = np.concatenate(parts) + noise(sum(len(p) for p in parts) * 1000 // RATE, -70, seed=9)
    pcm = np.clip(sig, -32768, 32767).astype(np.int16).tobytes()
    for i in range(0, len(pcm), 640):
        vad.push(pcm[i:i + 640])
    return vad.speech_ms


class NearFieldTest(unittest.TestCase):
    def test_a_loud_chime_first_does_not_hide_the_answer(self):
        # the chime, from the phone's speaker next to its mic, 15 dB above the voice: it set
        # the user's level and the rest of the answer counted as background
        heard = speech_ms(noise(300, -70), chime(-10), noise(400, -70), voice(2000, -25))
        self.assertGreater(heard, 1300)

    def test_a_quieter_voice_after_the_user_is_background(self):
        user = speech_ms(noise(300, -70), voice(1000, -20))
        with_tv = speech_ms(noise(300, -70), voice(1000, -20), voice(2000, -38, seed=2))
        self.assertLess(with_tv - user, 200)


if __name__ == "__main__":
    unittest.main()
