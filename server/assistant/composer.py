"""Response composer: the same answer, shaped for speech or for a chat."""

import re

EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
SPOKEN = [
    (r"(\d)\s*°\s*C\b", r"\1 degrees"), (r"(\d)\s*°\s*F\b", r"\1 degrees Fahrenheit"), (r"°", " degrees"),
    (r"(\d)\s*%", r"\1 percent"), (r"\bkm/h\b", "kilometers per hour"), (r"\bmph\b", "miles per hour"),
    (r"(\d)\s*km\b", r"\1 kilometers"), (r"(\d)\s*kg\b", r"\1 kilograms"), (r"(\d)\s*mm\b", r"\1 millimeters"),
    (r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"), (r"\betc\.", "and so on"),
    (r"&", " and "), (r"\s+/\s+", " or "),
]


SPOKEN_FR = [
    (r"(\d)\s*°\s*C\b", r"\1 degrés"), (r"°", " degrés"), (r"(\d)\s*%", r"\1 pour cent"),
    (r"\bkm/h\b", "kilomètres heure"), (r"(\d)\s*km\b", r"\1 kilomètres"), (r"(\d)\s*kg\b", r"\1 kilos"),
    (r"(\d)\s*mm\b", r"\1 millimètres"), (r"\bp\. ?ex\.", "par exemple"), (r"\betc\.", "et cetera"),
    (r"&", " et "), (r"\s+/\s+", " ou "),
]


# Interjections and laughter make the TTS model laugh, sigh or gasp; so do
# "!" and "...". Spoken replies drop them.
LAUGH = re.compile(r"\b(?:ha(?:ha)+|he(?:he)+|hi(?:hi)+|lol|mdr|xd)\b[\s,.!]*", re.I)
INTERJ = (r"(?:ha|oh(?: là là)?|ooh|ah+|hmm+|hm+|mm+|wow|whoa|ouh|euh|uh|um|hein|bah|ben|oups|oops|"
          r"yay|ouf|pfff*)")
STAGE = re.compile(r"\s*\*(?:laugh|giggl|chuckl|sigh|smil|grin|rire|rit\b|souri|soupir)[^*]{0,25}\*\s*", re.I)
LEAD_INTERJ = re.compile(r"(^|[.!?…]\s+)" + INTERJ + r"\b\s*[,.!?…]+\s*", re.I)


def calm(t):
    t = LAUGH.sub("", t)
    t = LEAD_INTERJ.sub(r"\1", t)
    t = STAGE.sub(" ", t)                                               # *laughs*
    t = re.sub(r"\s*[(\[](?:rires?|laughs?|laughing|sighs?|soupir|smiles?|sourire)[)\]]\s*", " ", t, flags=re.I)
    t = re.sub(r"\s*(?:\.\.\.|…)\s*(?=[A-ZÀ-Ý])", ". ", t)
    t = re.sub(r"\s*(?:\.\.\.|…)\s*", ", ", t)
    t = re.sub(r"\s*!+", ".", t)
    t = re.sub(r"\s*~+", "", t)
    t = re.sub(r"\.\s*,", ".", t)
    t = re.sub(r",\s*([.?])", r"\1", t)
    t = re.sub(r"(^|[.?]\s+)([a-zà-ý])", lambda m: m.group(1) + m.group(2).upper(), t.strip(" ,"))
    return t


def for_speech(text, lang="en"):
    t = text
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = calm(t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)          # links
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    t = EMOJI.sub("", t)
    for a, b in (SPOKEN_FR if lang == "fr" else SPOKEN):
        t = re.sub(a, b, t)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"\.\s*\.", ".", t)
    return re.sub(r"\s{2,}", " ", t).strip()


class SentenceSplitter:
    """Cuts a token stream into sentences as early as possible so the first
    one can be spoken while the rest is still being generated."""

    END = re.compile(r"([.!?…]+[\"')\]]?)(\s+)(?=[A-Z0-9\"'(])|(\n+)")

    def __init__(self):
        self.buf = ""
        self.count = 0

    def push(self, text):
        self.buf += text
        out = []
        while True:
            m = self.END.search(self.buf)
            if not m:
                break
            cut = m.end(1) if m.group(1) else m.start(3)
            sent = self.buf[:cut].strip()
            # don't cut after abbreviations / very short fragments
            if len(sent) < 12 and m.group(1) and self.count > 0:
                break
            if re.search(r"\b(Mr|Mrs|Ms|Dr|St|vs|No)\.$", sent):
                break
            self.buf = self.buf[m.end():]
            if sent:
                out.append(sent)
                self.count += 1
        # a long clause without punctuation: cut at a comma
        if len(self.buf) > 160:
            i = self.buf.rfind(", ", 0, 160)
            if i > 40:
                out.append(self.buf[:i + 1].strip())
                self.buf = self.buf[i + 2:]
                self.count += 1
        return out

    def flush(self):
        s, self.buf = self.buf.strip(), ""
        return [s] if s else []
