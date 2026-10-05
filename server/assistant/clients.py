"""Clients of the OpenAI-compatible speech-to-text, text-to-speech and LLM
services."""

import base64
import collections
import json
import logging
import threading
import time

import numpy as np
import requests

from .audio import read_wav, wav_bytes

log = logging.getLogger("clients")


def sound_seconds(pcm, rate):
    """Seconds of sound (20 ms frames within 35 dB of the loudest), pauses excluded."""
    hop = rate // 50
    x = np.frombuffer(pcm[:len(pcm) // (2 * hop) * 2 * hop], np.int16).astype(np.float32)
    if not len(x):
        return 0.0
    e = 10 * np.log10((x.reshape(-1, hop) ** 2).mean(1) + 1e-3)
    return float((e > e.max() - 35).sum()) / 50


class _Base:
    section = ""

    def __init__(self, settings):
        self.settings = settings
        self.http = requests.Session()
        self.health = {"ok": None, "ms": None, "error": None, "checked": 0}

    @property
    def cfg(self):
        return self.settings[self.section]

    def _headers(self):
        tok = self.cfg.get("token")
        return {"Authorization": "Bearer " + tok} if tok else {}

    def _url(self, path):
        return self.cfg["url"].rstrip("/") + path

    def models(self):
        r = self.http.get(self._url("/models"), headers=self._headers(), timeout=5)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    def check(self):
        t0 = time.time()
        try:
            ids = self.models()
            self.health = {"ok": True, "ms": round((time.time() - t0) * 1000), "error": None,
                           "checked": time.time(), "models": ids}
        except Exception as e:
            self.health = {"ok": False, "ms": None, "error": str(e)[:200], "checked": time.time()}
        return self.health


class STT(_Base):
    section = "stt"

    MIN_PIECE_S = 1.5

    def _post(self, pcm, rate, language):
        data = {"model": self.cfg["model"], "response_format": "json"}
        if language:
            data["language"] = language
        files = {"file": ("audio.wav", wav_bytes(pcm, rate), "audio/wav")}
        return self.http.post(self._url("/audio/transcriptions"), headers=self._headers(), files=files,
                              data=data, timeout=self.cfg.get("timeout", 20))

    def transcribe(self, pcm, rate=16000, language=None):
        """16-bit mono PCM -> text."""
        return self._piece(np.frombuffer(pcm, np.int16), rate, language, top=True)

    def _piece(self, x, rate, language, top=False):
        r = self._post(x.tobytes(), rate, language)
        if r.status_code >= 500 and len(x) > 2 * rate * self.MIN_PIECE_S:
            # a GPU short on memory fails on long audio (the buffers grow with the
            # length): two halves, cut at the quietest point near the middle
            if top:
                log.warning("STT failed on %.1f s of audio (%s), retrying in pieces", len(x) / rate,
                            r.text[:160])
            hop = rate // 50
            lo, hi = int(len(x) * 0.35) // hop, int(len(x) * 0.65) // hop
            e = (x[lo * hop:hi * hop].astype(np.float32).reshape(-1, hop) ** 2).mean(1)
            cut = (lo + int(np.argmin(e))) * hop
            parts = [self._piece(x[:cut], rate, language), self._piece(x[cut:], rate, language)]
            return " ".join(t for t in parts if t)
        for pad_s in (0.4, 0.9, 0.25, 0.6, 1.2):   # short audio: some lengths fail, others fit
            if r.status_code < 500:
                break
            r = self._post(np.concatenate([x, np.zeros(int(rate * pad_s), np.int16)]).tobytes(), rate, language)
        r.raise_for_status()
        return (r.json().get("text") or "").strip()


class TTS(_Base):
    section = "tts"
    RATE = 24000

    def __init__(self, settings):
        super().__init__(settings)
        self.cache = collections.OrderedDict()
        self.cache_bytes = 0
        self.lock = threading.Lock()

    LANGS = {"en": "English", "fr": "French"}

    def stream(self, text, voice=None, cancel=None, lang=None):
        """Yields 24 kHz s16le mono chunks as they are synthesized."""
        voice = voice or self.cfg["voice"]
        key = (voice, lang, text.strip().lower())
        cacheable = self.cfg.get("cache_phrases", True) and len(text) <= 80
        if cacheable:
            with self.lock:
                hit = self.cache.get(key)
                if hit:
                    self.cache.move_to_end(key)
            if hit:
                yield hit
                return
        body = {"model": self.cfg["model"], "voice": voice, "input": text}
        if self.cfg.get("instructions"):
            body["instructions"] = self.cfg["instructions"]
        if lang in self.LANGS:
            body["language"] = self.LANGS[lang]      # steadier delivery than auto-detection
        budget = self.budget_s(text)
        # hard stop for runaways (12.5 codec frames per second)
        body["max_new_tokens"] = int(12.5 * (budget * 1.6 + 1.0)) + 1
        if not self.cfg.get("check_takes", True):
            got = []
            for pcm in self._generate(body, cancel):
                got.append(pcm)
                yield pcm
            data = b"".join(got)
        else:
            # The model sometimes hums, giggles or keeps going around the words:
            # each sentence is generated in full (5x faster than real time) and a
            # take with more sound than the text can need is generated again.
            best = None
            for take in range(3):
                data = b"".join(self._generate(body, cancel))
                if cancel is not None and cancel.is_set():
                    return
                sound = sound_seconds(data, self.RATE)
                if best is None or sound < best[0]:
                    best = (sound, data)
                if sound <= budget:
                    break
                log.info("TTS take %d of %r: %.1f s of sound for a %.1f s budget, again", take + 1,
                         text[:60], sound, budget)
            data = best[1]
            for i in range(0, len(data), self.RATE // 2):      # 0.25 s chunks
                yield data[i:i + self.RATE // 2]
            if best[0] > budget:
                return                                           # don't cache a bad take
        if cacheable and data:
            with self.lock:
                self.cache[key] = data
                self.cache_bytes += len(data)
                while self.cache_bytes > 24 * 1024 * 1024 and self.cache:
                    _, old = self.cache.popitem(last=False)
                    self.cache_bytes -= len(old)

    @staticmethod
    def budget_s(text):
        """The most sound a sentence can need: ~16 characters per second, numbers
        said in full, and a margin."""
        digits = sum(c.isdigit() for c in text)
        return len(text) / 16 + digits * 0.25 + 0.5

    def _generate(self, body, cancel):
        if self.cfg.get("stream", True):
            body = dict(body, response_format="pcm", stream=True)
            with self.http.post(self._url("/audio/speech"), headers=self._headers(), json=body,
                                stream=True, timeout=self.cfg.get("timeout", 60)) as r:
                r.raise_for_status()
                if "event-stream" not in r.headers.get("content-type", ""):
                    yield r.content
                    return
                for line in r.iter_lines(chunk_size=8192):
                    if cancel is not None and cancel.is_set():
                        return
                    if not line or not line.startswith(b"data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    if ev.get("audio"):
                        yield base64.b64decode(ev["audio"])
        else:
            body = dict(body, response_format="wav")
            r = self.http.post(self._url("/audio/speech"), headers=self._headers(), json=body,
                               timeout=self.cfg.get("timeout", 60))
            r.raise_for_status()
            yield read_wav(r.content)[0]

    def synthesize(self, text, voice=None, lang=None):
        return b"".join(self.stream(text, voice, lang=lang))

    def voices(self):
        try:
            r = self.http.get(self._url("/audio/voices"), headers=self._headers(), timeout=5)
            return r.json().get("voices", [])
        except Exception:
            return []


class LLM(_Base):
    section = "llm"

    def __init__(self, settings):
        super().__init__(settings)
        self.resolved = None

    def model(self):
        want = self.cfg["model"]
        if self.resolved and self.resolved[0] == want:
            return self.resolved[1]
        try:
            ids = self.models()
            use = want if want in ids or not ids else ids[0]
            if use != want:
                log.warning("LLM model %r not served, using %r", want, use)
        except Exception:
            use = want
        self.resolved = (want, use)
        return use

    def stream(self, messages, tools=None, cancel=None, max_tokens=None, temperature=None):
        """Yields ("text", str) deltas, then ("tools", [calls]) if any, then ("done", reason)."""
        c = self.cfg
        body = {"model": self.model(), "messages": messages, "stream": True,
                "max_tokens": max_tokens or c.get("max_tokens", 600),
                "temperature": c.get("temperature", 0.6) if temperature is None else temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if c.get("disable_thinking", True):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        calls = {}
        reason = None
        in_think = False
        with self.http.post(self._url("/chat/completions"), headers=self._headers(), json=body,
                            stream=True, timeout=c.get("timeout", 90)) as r:
            if r.status_code >= 400:
                raise RuntimeError("LLM %s: %s" % (r.status_code, r.text[:300]))
            for line in r.iter_lines():
                if cancel is not None and cancel.is_set():
                    return
                if not line or not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                for ch in ev.get("choices", []):
                    d = ch.get("delta", {})
                    txt = d.get("content")
                    if txt:
                        # drop reasoning blocks if the model emits them inline
                        if "<think>" in txt:
                            in_think = True
                            txt = txt.split("<think>")[0]
                        if in_think:
                            if "</think>" in txt:
                                in_think = False
                                txt = txt.split("</think>", 1)[1]
                            else:
                                txt = ""
                        if txt:
                            yield "text", txt
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        reason = ch["finish_reason"]
        if calls:
            yield "tools", [calls[i] for i in sorted(calls)]
        yield "done", reason

    def complete(self, messages, **kw):
        out = []
        for kind, val in self.stream(messages, **kw):
            if kind == "text":
                out.append(val)
        return "".join(out).strip()
