"""Microphone capture, playback mixer and earcons.

- Mic: 16 kHz mono s16le frames of 20 ms for the server. The device is opened
  at 16 kHz when it supports it, else at its native rate and resampled.
- Mixer: one output stream; each server stream (stream_open) has its own
  buffer, resampled to the output rate. Music is ducked under speech (-18 dB)
  and while the microphone is open (-40 dB), as PROTOCOL.md section 3 asks.
- Earcons: short tones synthesized with numpy.
"""

import collections
import logging
import math
import threading
import time

import numpy as np

try:
    import sounddevice as sd
except (ImportError, OSError) as e:     # PortAudio missing
    sd = None
    SD_ERROR = str(e)
else:
    SD_ERROR = ""

log = logging.getLogger("audio")

MIC_RATE = 16000
MIC_FRAME = 320                 # 20 ms at 16 kHz
SPEECH_KINDS = ("tts", "alarm", "chime")


def db_to_gain(db):
    return 10 ** (db / 20)


def pcm_to_float(pcm, channels):
    """s16le bytes -> float32 array (frames, channels)."""
    a = np.frombuffer(pcm[:len(pcm) - len(pcm) % (2 * channels)], dtype="<i2")
    return (a.astype(np.float32) / 32768.0).reshape(-1, channels)


def to_stereo(x):
    if x.shape[1] == 2:
        return x
    if x.shape[1] == 1:
        return np.repeat(x, 2, axis=1)
    return x[:, :2]


def find_device(name, kind):
    """Index of the sounddevice device whose name contains `name`, or None
    (system default)."""
    if not name or sd is None:
        return None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d[key] > 0:
            return i
    log.warning("no %s device named %r, using the default", kind, name)
    return None


class Resampler:
    """Streaming linear-interpolation resampler for float32 (frames, channels)
    blocks. Downsampling first goes through a small windowed-sinc low-pass so
    the microphone doesn't alias."""

    def __init__(self, src, dst, channels=1):
        self.src, self.dst = int(src), int(dst)
        self.ratio = self.src / self.dst
        self.pos = 0.0
        self.tail = None
        self.taps = None
        if self.ratio > 1.0:
            n = 31
            fc = 0.45 / self.ratio
            k = np.arange(n) - (n - 1) / 2
            h = 2 * fc * np.sinc(2 * fc * k) * np.blackman(n)
            self.taps = (h / h.sum()).astype(np.float32)
            self.hist = np.zeros((n - 1, channels), dtype=np.float32)

    def process(self, x):
        if self.src == self.dst or len(x) == 0:
            return x
        if self.taps is not None:
            buf = np.concatenate([self.hist, x])
            self.hist = buf[-(len(self.taps) - 1):]
            x = np.stack([np.convolve(buf[:, c], self.taps, mode="valid") for c in range(x.shape[1])],
                         axis=1).astype(np.float32)
        if self.tail is not None:
            x = np.concatenate([self.tail, x])
        n = len(x)
        if n < 2:
            self.tail = x
            return x[:0]
        k = max(0, math.ceil((n - 1 - self.pos) / self.ratio))
        p = self.pos + np.arange(k) * self.ratio
        i = p.astype(np.int64)
        f = (p - i).astype(np.float32)[:, None]
        out = x[i] * (1 - f) + x[i + 1] * f
        self.pos = self.pos + k * self.ratio - (n - 1)
        self.tail = x[-1:]
        return out.astype(np.float32)


# ---------------------------------------------------------------- microphone
class Mic:
    """Opened only while listening. on_frame(bytes) gets 20 ms 16 kHz frames
    from the audio thread; `level` (0..1) drives the overlay's animation."""

    def __init__(self, on_frame, device_name=""):
        self.on_frame = on_frame
        self.device_name = device_name
        self.stream = None
        self.level = 0.0
        self.rate = MIC_RATE
        self._buf = np.zeros(0, dtype=np.float32)
        self._rs = None

    def start(self):
        if sd is None:
            raise RuntimeError("audio unavailable (%s)" % SD_ERROR)
        self.stop()
        dev = find_device(self.device_name, "input")
        try:
            sd.check_input_settings(device=dev, samplerate=MIC_RATE, channels=1, dtype="float32")
            self.rate = MIC_RATE
        except Exception:
            info = sd.query_devices(dev if dev is not None else sd.default.device[0])
            self.rate = int(info["default_samplerate"])
        self._rs = Resampler(self.rate, MIC_RATE) if self.rate != MIC_RATE else None
        self._buf = np.zeros(0, dtype=np.float32)
        self.stream = sd.InputStream(device=dev, samplerate=self.rate, channels=1, dtype="float32",
                                     blocksize=int(self.rate * 0.02), callback=self._callback)
        self.stream.start()
        log.debug("microphone open at %d Hz", self.rate)

    def stop(self):
        s, self.stream = self.stream, None
        if s:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self.level = 0.0

    @property
    def active(self):
        return self.stream is not None

    def _callback(self, indata, frames, t, status):
        x = indata[:, :1].astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        db = 20 * math.log10(rms)
        target = min(1.0, max(0.0, (db + 60) / 45))
        self.level = max(target, self.level * 0.85)     # fast attack, slow release
        if self._rs:
            x = self._rs.process(x)
        self.feed(x[:, 0])

    def feed(self, mono16k):
        """Cuts 16 kHz float samples into 20 ms s16le frames."""
        self._buf = np.concatenate([self._buf, mono16k])
        while len(self._buf) >= MIC_FRAME:
            frame, self._buf = self._buf[:MIC_FRAME], self._buf[MIC_FRAME:]
            pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            try:
                self.on_frame(pcm)
            except Exception:
                log.exception("mic frame handler failed")


# ---------------------------------------------------------------- earcons
def _note(rate, freq, dur, amp, attack=0.004, decay=7.0, freq_end=None):
    t = np.arange(int(rate * dur), dtype=np.float32) / rate
    if freq_end:
        f = np.linspace(freq, freq_end, len(t), dtype=np.float32)
        phase = 2 * np.pi * np.cumsum(f) / rate
    else:
        phase = 2 * np.pi * freq * t
    env = (1 - np.exp(-t / attack)) * np.exp(-t * decay)
    tail = min(len(t), int(rate * 0.01))           # no click at the end
    if tail:
        env[-tail:] *= np.linspace(1, 0, tail)
    wave = np.sin(phase) + 0.22 * np.sin(2 * phase) + 0.06 * np.sin(3 * phase)
    return (amp * env * wave).astype(np.float32)


def _pip(rate, freq, dur, amp, rise=0.012, fall=0.045):
    """A soft electronic beep: an almost pure sine with rounded (raised-cosine)
    edges. Harshness comes from fast edges and upper harmonics: none here."""
    n = int(rate * dur)
    t = np.arange(n, dtype=np.float32) / rate
    env = np.ones(n, dtype=np.float32)
    a, r = int(rate * rise), int(rate * fall)
    env[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    env[n - r:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    phase = 2 * np.pi * freq * t
    wave = np.sin(phase) + 0.05 * np.sin(2 * phase)
    return (amp * env * wave).astype(np.float32)


def _room(x, rate, delay=0.045, gain=0.2):
    """One faint echo, so a beep doesn't sound dry."""
    d = int(rate * delay)
    out = np.zeros(len(x) + d, dtype=np.float32)
    out[:len(x)] += x
    out[d:] += gain * x
    return out


def _place(parts, rate):
    """[(start_s, samples)] -> one buffer."""
    n = max(int(s * rate) + len(x) for s, x in parts)
    out = np.zeros(n, dtype=np.float32)
    for s, x in parts:
        i = int(s * rate)
        out[i:i + len(x)] += x
    return out


def make_earcon(name, rate):
    if name == "done":          # two-note rising chime (C6 -> G6)
        return _place([(0, _note(rate, 1046.5, 0.35, 0.30)), (0.11, _note(rate, 1568.0, 0.55, 0.26, decay=5))], rate)
    if name == "error":         # two soft falling notes
        return _place([(0, _note(rate, 392.0, 0.30, 0.30, decay=8)), (0.17, _note(rate, 311.1, 0.45, 0.30, decay=6))],
                      rate)
    if name in ("listen", "wake"):  # two soft "pip"s when the microphone opens
        pip = _pip(rate, 1318.5, 0.11, 0.17)
        return _room(_place([(0, pip), (0.18, pip)], rate), rate)
    if name == "listen_end":    # the same, falling, when it closes
        return _note(rate, 880, 0.14, 0.10, attack=0.02, decay=10, freq_end=600)
    # notify and anything unknown: three bright notes
    return _place([(0, _note(rate, 784.0, 0.3, 0.22)), (0.1, _note(rate, 987.8, 0.3, 0.22)),
                   (0.2, _note(rate, 1174.7, 0.5, 0.22, decay=5))], rate)


# ---------------------------------------------------------------- playback
class PlayStream:
    def __init__(self, sid, kind, rate, channels, gain_db, out_rate, prebuffer_ms):
        self.id = sid
        self.kind = kind
        self.rate = int(rate)
        self.channels = max(1, int(channels))
        self.gain = db_to_gain(float(gain_db or 0))
        self.rs = Resampler(self.rate, out_rate, 2)
        self.chunks = collections.deque()
        self.offset = 0             # read position in chunks[0]
        self.avail = 0              # frames buffered (output rate)
        self.prebuffer = int(out_rate * min(max(prebuffer_ms, 50), 1000) / 1000)
        self.started = False
        self.closing = False
        self.paused = False
        self.received = 0           # bytes, for the debug CLI

    def push(self, x):
        if len(x):
            self.chunks.append(x)
            self.avail += len(x)

    def pull(self, n):
        out = np.zeros((n, 2), dtype=np.float32)
        got = 0
        while got < n and self.chunks:
            c = self.chunks[0]
            take = min(n - got, len(c) - self.offset)
            out[got:got + take] = c[self.offset:self.offset + take]
            got += take
            self.offset += take
            if self.offset >= len(c):
                self.chunks.popleft()
                self.offset = 0
        self.avail -= got
        return out, got


class Mixer:
    """Playback of the server's streams and of the earcons.

    With enabled=False nothing is played (debug CLI --no-audio): streams are
    counted and finish as soon as they are closed."""

    def __init__(self, device_name="", enabled=True, on_event=None):
        self.device_name = device_name
        self.enabled = enabled and sd is not None
        self.on_event = on_event        # (stream_id, "started" | "finished"), from the audio thread
        self.lock = threading.RLock()
        self.streams = {}
        self.earcons = []               # [samples (mono), position, gain]
        self.out = None
        self.out_rate = 48000
        self.out_channels = 2
        self.volume = 0.8
        self.mic_open = False
        self.duck = 1.0
        self.level = 0.0
        self.last_sound = time.monotonic()
        self._cache = {}
        self.stats = {}                 # stream id -> bytes received (debug CLI)
        if self.enabled:
            threading.Thread(target=self._housekeeping, name="mixer", daemon=True).start()

    # ------------------------------------------------------------ control
    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, float(v) / 100)) ** 2     # perceptual-ish curve

    def set_mic_open(self, is_open):
        self.mic_open = bool(is_open)

    def open(self, sid, kind="tts", rate=24000, channels=1, gain_db=0, prebuffer_ms=None):
        if prebuffer_ms is None:
            prebuffer_ms = 150 if kind != "media" else 300
        with self.lock:
            self._ensure_output()
            self.streams[sid] = PlayStream(sid, kind, rate, channels, gain_db, self.out_rate, prebuffer_ms)
            self.stats[sid] = 0

    def feed(self, sid, pcm):
        with self.lock:
            s = self.streams.get(sid)
            if s is None:
                return
            s.received += len(pcm)
            self.stats[sid] = s.received
            if not self.out:
                return              # nothing to play on (disabled or no device)
            s.push(s.rs.process(to_stereo(pcm_to_float(pcm, s.channels))))
            self.last_sound = time.monotonic()

    def close(self, sid, drain=True):
        events = []
        with self.lock:
            s = self.streams.get(sid)
            if s is None:
                return
            if drain and self.out and s.avail > 0:
                s.closing = True
            else:
                del self.streams[sid]
                events.append((sid, "finished"))
        self._emit(events)

    def pause(self, sid, paused):
        with self.lock:
            s = self.streams.get(sid)
            if s:
                s.paused = bool(paused)

    def stop(self, kinds=None):
        """Drops the streams of these kinds (all if None) right away."""
        events = []
        with self.lock:
            for sid, s in list(self.streams.items()):
                if kinds is None or s.kind in kinds:
                    del self.streams[sid]
                    events.append((sid, "finished"))
            if kinds is None:
                self.earcons = []
        self._emit(events)

    def busy(self, kinds=SPEECH_KINDS):
        """Something of these kinds is still open or buffered."""
        with self.lock:
            return any(s.kind in kinds for s in self.streams.values())

    def has_media(self):
        return self.busy(("media",))

    def earcon(self, name, gain=0.6):
        with self.lock:
            self._ensure_output()
            if not self.out:
                return
            key = (name, self.out_rate)
            if key not in self._cache:
                self._cache[key] = make_earcon(name, self.out_rate)
            self.earcons.append([self._cache[key], 0, gain])
            self.last_sound = time.monotonic()

    def close_output(self):
        with self.lock:
            out, self.out = self.out, None
        if out:
            try:
                out.stop()
                out.close()
            except Exception:
                pass

    # ------------------------------------------------------------ internals
    def _ensure_output(self):
        if self.out or not self.enabled:
            return
        dev = find_device(self.device_name, "output")
        try:
            rate, channels = 48000, 2
            try:
                sd.check_output_settings(device=dev, samplerate=rate, channels=channels, dtype="float32")
            except Exception:
                info = sd.query_devices(dev if dev is not None else sd.default.device[1])
                rate = int(info["default_samplerate"])
                channels = min(2, int(info["max_output_channels"])) or 1
            out = sd.OutputStream(device=dev, samplerate=rate, channels=channels, dtype="float32",
                                  latency=0.1, callback=self._callback)
            out.start()
        except Exception as e:
            log.error("cannot open the audio output: %s", e)
            self.enabled = False        # don't retry at every message
            return
        if rate != self.out_rate:
            # streams opened before (none in practice) would have the wrong rate
            self.streams.clear()
            self._cache.clear()
        self.out, self.out_rate, self.out_channels = out, rate, channels
        log.debug("audio output open at %d Hz, %d ch", rate, channels)

    def _housekeeping(self):
        # release the output device after a while of silence
        while True:
            time.sleep(5)
            with self.lock:
                idle = not self.streams and not self.earcons and time.monotonic() - self.last_sound > 20
            if idle and self.out:
                self.close_output()

    def _emit(self, events):
        if self.on_event:
            for sid, ev in events:
                try:
                    self.on_event(sid, ev)
                except Exception:
                    log.exception("playback event handler failed")

    def render(self, frames):
        """Mixes the next `frames` frames (stereo float32). Also used by the
        tests without an output device."""
        mix = np.zeros((frames, 2), dtype=np.float32)
        events = []
        with self.lock:
            speech = any(s.kind in SPEECH_KINDS and not s.paused for s in self.streams.values())
            target = db_to_gain(-40) if self.mic_open else db_to_gain(-18) if speech else 1.0
            alpha = 1 - math.exp(-frames / (self.out_rate * 0.08))
            new = self.duck + (target - self.duck) * alpha
            ramp = np.linspace(self.duck, new, frames, dtype=np.float32)[:, None]
            self.duck = new
            for sid, s in list(self.streams.items()):
                if s.paused:
                    continue
                if not s.started:
                    if s.avail >= s.prebuffer or (s.closing and s.avail > 0):
                        s.started = True
                        events.append((sid, "started"))
                    else:
                        continue
                x, got = s.pull(frames)
                if got:
                    mix += x * (s.gain * ramp if s.kind == "media" else s.gain)
                if s.closing and s.avail <= 0:
                    del self.streams[sid]
                    events.append((sid, "finished"))
            for e in list(self.earcons):
                buf, pos, g = e
                take = min(frames, len(buf) - pos)
                mix[:take] += buf[pos:pos + take, None] * g
                e[1] += take
                if e[1] >= len(buf):
                    self.earcons.remove(e)
            if self.streams or self.earcons:
                self.last_sound = time.monotonic()
        mix *= self.volume
        np.clip(mix, -1, 1, out=mix)
        rms = float(np.sqrt(np.mean(mix * mix))) if frames else 0.0
        self.level = max(min(1.0, rms * 4), self.level * 0.8)
        self._emit(events)
        return mix

    def _callback(self, outdata, frames, t, status):
        mix = self.render(frames)
        if self.out_channels == 1:
            outdata[:, 0] = mix.mean(axis=1)
        else:
            outdata[:, :2] = mix
            if outdata.shape[1] > 2:
                outdata[:, 2:] = 0
