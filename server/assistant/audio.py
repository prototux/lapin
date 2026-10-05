"""PCM helpers: WAV containers, a frame VAD, server-side denoising, ffmpeg."""

import io
import subprocess
import wave

import numpy as np


def wav_bytes(pcm, rate=16000, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def read_wav(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()


def to_float(pcm):
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def to_pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def level_db(pcm):
    x = to_float(pcm)
    return float(10 * np.log10(np.mean(x * x) + 1e-12)) if len(x) else -120.0


class Vad:
    """VAD on 20 ms frames (16 kHz): a frame is speech when it is loud enough
    above an adaptive noise floor AND sounds like speech to the WebRTC
    detector (spectral shape, not just level: a phone's automatic gain lifts
    the room's noise to speech levels)."""

    FRAME = 320

    def __init__(self, margin_db=9.0, hangover=8, mode=3, near_field=False, near_drop_db=12.0):
        try:
            import webrtcvad
            self.shape = webrtcvad.Vad(mode)
        except Exception:           # not installed: level only
            self.shape = None
        self.floor = None
        # near field (a phone held in the hand): once the user has spoken, voices
        # much quieter than theirs (TV, announcements, colleagues) are background
        self.near_field = near_field
        self.near_drop = near_drop_db
        self.user_db = None
        self.user_ms = 0
        self.margin = margin_db
        self.hangover = hangover
        self.hang = 0
        self.rest = b""
        self.speech_frames = 0
        self.silence_ms = 0
        self.total_ms = 0
        self.speech_ms = 0
        self.last_speech_ms = None

    def push(self, pcm):
        """Feeds audio; returns True if the last frame is speech."""
        data = self.rest + pcm
        n = len(data) // (2 * self.FRAME)
        self.rest = data[n * 2 * self.FRAME:]
        speech = self.hang > 0
        for i in range(n):
            frame = data[i * 2 * self.FRAME:(i + 1) * 2 * self.FRAME]
            db = level_db(frame)
            # noise floor: quick down, slow up (faster in the first second,
            # so it reaches the room's level before the end of a short request)
            if self.floor is None:
                self.floor = min(db, -45.0)
            up = 0.03 if self.total_ms < 1000 else 0.005
            self.floor += (db - self.floor) * (0.3 if db < self.floor else up)
            self.total_ms += 20
            loud = db > self.floor + self.margin and db > -58
            if loud and self.shape is not None:
                try:
                    loud = self.shape.is_speech(frame, 16000)
                except Exception:
                    pass
            if loud and self.near_field:
                # the user's level: a peak of speech frames, slowly decaying
                # (1 dB/s), so a quieter end of sentence still counts
                if self.user_db is None or db > self.user_db:
                    self.user_db = db
                if self.user_ms >= 300 and db < self.user_db - self.near_drop:
                    loud = False                # someone farther away
                else:
                    self.user_ms += 20
            if self.user_db is not None:
                self.user_db -= 0.02
            if loud:
                self.hang = self.hangover
                self.speech_frames += 1
                self.speech_ms += 20
                self.last_speech_ms = self.total_ms
            elif self.hang > 0:
                self.hang -= 1
            speech = self.hang > 0
            self.silence_ms = 0 if speech else self.silence_ms + 20
        return speech


def denoise(pcm, rate=16000, floor_db=-15):
    """Spectral gating: noise PSD from the quietest frames, Wiener gain."""
    x = to_float(pcm)
    n, hop = 512, 256
    if len(x) < n * 4:
        return pcm
    win = np.sqrt(np.hanning(n + 1)[:n])
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(x, (n, n)), n)[::hop] * win
    X = np.fft.rfft(frames, axis=1)
    P = np.abs(X) ** 2
    e = P.sum(1)
    quiet = P[e <= np.percentile(e, 15)]
    N = quiet.mean(0) + 1e-10
    snr = np.maximum(P / N - 1, 0)
    G = np.maximum(snr / (snr + 1), 10 ** (floor_db / 20))
    Y = np.fft.irfft(X * G, n, axis=1) * win
    out = np.zeros(len(x) + 2 * n + n)
    for i, f in enumerate(Y):
        out[i * hop:i * hop + n] += f
    return to_pcm(out[n:n + len(x)])


def ffmpeg(data, in_args, out_args, timeout=30):
    """Runs ffmpeg on bytes: in_args/out_args are lists of options."""
    cmd = ["ffmpeg", "-loglevel", "error", "-nostdin", *in_args, "-i", "pipe:0", *out_args, "pipe:1"]
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("ffmpeg: " + r.stderr.decode(errors="replace").strip()[:300])
    return r.stdout


def decode_to_pcm16k(data):
    """Any audio file (ogg/opus voice notes, mp3, wav...) -> 16 kHz mono s16le."""
    return ffmpeg(data, [], ["-f", "s16le", "-ac", "1", "-ar", "16000"])


def pcm_to_ogg_opus(pcm, rate=24000):
    return ffmpeg(pcm, ["-f", "s16le", "-ac", "1", "-ar", str(rate)],
                  ["-c:a", "libopus", "-b:a", "32k", "-f", "ogg"])
