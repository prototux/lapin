"""Hotword verifier (stage 2): the satellite's detector is tuned to miss
little; here the pre-roll audio (which holds the wake word) is transcribed by
the speech recognizer and compared with the wake phrases, phonetically and
fuzzily, to reject false wakes (TV, similar words).

Recognizers spell made-up names in many ways ("Lapin" -> "Lapen", "Le Pen",
"lapping"), so each enrollment sample recorded on a satellite is transcribed
too and kept as an alias of its wake word."""

import difflib
import logging
import re
import threading

from .audio import read_wav

log = logging.getLogger("verifier")


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z' ]+", " ", text.lower())).strip()


def phonetic(word):
    """Crude English phonetic key: similar consonants merged, vowels folded."""
    w = word.lower()
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("wh", "w"), ("th", "t"), ("sh", "s"), ("ch", "s"),
                 ("gh", "g"), ("kn", "n"), ("x", "ks")):
        w = w.replace(a, b)
    table = str.maketrans("bpdtgkcqvfszjmnlrwyh", "ppttkkkkffsssmmlrwy ")
    w = w.translate(table).replace(" ", "")
    out = []
    for i, ch in enumerate(w):
        if ch in "aeiou":
            ch = "a"            # all vowels alike
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def similarity(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    raw = difflib.SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio()
    pa = "".join(phonetic(w) for w in a.split())
    pb = "".join(phonetic(w) for w in b.split())
    ph = difflib.SequenceMatcher(None, pa, pb).ratio()
    return max(raw, ph * 0.95 + raw * 0.05)


def best_window(transcript, phrase):
    """Best similarity of the phrase with any window of words of the
    transcript (+-1 word); returns (score, end word index)."""
    words = norm(transcript).split()
    n = len(norm(phrase).split()) or 1
    best = (0.0, 0)
    for size in (n - 1, n, n + 1):
        if size < 1:
            continue
        for i in range(0, max(1, len(words) - size + 1)):
            seg = " ".join(words[i:i + size])
            if len(seg.replace(" ", "")) < 0.6 * len(norm(phrase).replace(" ", "")):
                continue
            s = similarity(seg, phrase)
            if s > best[0]:
                best = (s, i + size)
    return best


class Verifier:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()

    def phrases(self, device_words=()):
        """Wake phrases to accept: the configured ones, the words enrolled on the
        device, and the learned spellings of those (not of deleted words)."""
        w = self.app.settings["wake"]
        out = set(norm(p) for p in w.get("phrases", []))
        aliases = w.get("aliases", {})
        for kw in device_words:
            out.add(norm(kw.replace("_", " ")))
            out.update(norm(a) for a in aliases.get(kw, []) if self.good_alias(a, kw))
        return [p for p in out if p]

    @staticmethod
    def good_alias(alias, keyword):
        """A spelling is kept only if it looks and sounds like the wake word
        (a recognizer that heard "hi" for "hey bunny" must not teach us "hi")."""
        a, k = norm(alias), norm(keyword.replace("_", " "))
        if not a or len(a.replace(" ", "")) < 0.6 * len(k.replace(" ", "")):
            return False
        return similarity(a, k) >= 0.8

    def check(self, transcript, device_words=()):
        """(accepted, score, matched phrase, end word index)."""
        best = (0.0, "", 0)
        if len(norm(transcript).replace(" ", "")) < 3:
            return False, 0.0, "", 0
        for p in self.phrases(device_words):
            s, end = best_window(transcript, p)
            if s > best[0]:
                best = (s, p, end)
        thr = self.app.settings["wake"].get("threshold", 0.75)
        return best[0] >= thr, round(best[0], 3), best[1], best[2]

    def verify(self, pcm, device_words=()):
        text = self.app.stt.transcribe(pcm)
        ok, score, phrase, end = self.check(text, device_words)
        return {"accepted": ok, "score": score, "phrase": phrase, "transcript": text, "end": end}

    def strip_wake(self, transcript, device_words=()):
        """Removes the wake phrase (and anything before it) from the start of
        a transcript of pre-roll + command."""
        words = transcript.split()
        best = (0.0, 0)
        for p in self.phrases(device_words):
            n = len(p.split())
            for size in (n - 1, n, n + 1):
                for i in range(0, min(4, max(1, len(words) - size + 1))):
                    if size < 1:
                        continue
                    s = similarity(" ".join(words[i:i + size]), p)
                    if s > best[0]:
                        best = (s, i + size)
        if best[0] >= self.app.settings["wake"].get("threshold", 0.75):
            rest = " ".join(words[best[1]:]).lstrip(" ,.!?;:-")
            return rest[:1].upper() + rest[1:] if rest else ""
        return transcript

    def learn(self, keyword, wav):
        """Transcribes an enrollment sample and keeps it as an alias."""
        if not self.app.settings["wake"].get("learn_aliases", True):
            return None
        try:
            pcm, rate, ch = read_wav(wav)
            text = norm(self.app.stt.transcribe(pcm, rate))
        except Exception as e:
            log.warning("could not transcribe a wake word sample: %s", e)
            return None
        if not text or len(text) > 40 or not self.good_alias(text, keyword):
            log.info("wake word %s: sample heard as %r, not kept as a spelling", keyword, text)
            return None
        with self.lock:
            aliases = dict(self.app.settings["wake"].get("aliases", {}))
            lst = list(aliases.get(keyword, []))
            if text not in lst and text != norm(keyword.replace("_", " ")):
                lst = (lst + [text])[-12:]
                aliases[keyword] = lst
                self.app.settings.update({"wake": {"aliases": aliases}})
        log.info("wake word %s: learned alias %r", keyword, text)
        return text
