"""English number words, durations and clock times, and how to say them."""

import datetime as dt
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:      # pragma: no cover
    ZoneInfo = None

UNITS = {"zero": 0, "oh": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19, "couple": 2, "few": 3}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}
UNIT_SECONDS = {"second": 1, "sec": 1, "secs": 1, "s": 1, "minute": 60, "min": 60, "mins": 60,
                "hour": 3600, "hr": 3600, "hrs": 3600, "h": 3600, "day": 86400}


def words_to_numbers(text):
    """'set a timer for twenty five minutes' -> 'set a timer for 25 minutes'."""
    tokens = re.findall(r"\d+[:.]\d+|[\w']+|[^\w\s]", text.lower())
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t in TENS or (t in UNITS and t not in ("a", "an", "couple", "few", "oh")):
            n = TENS.get(t, UNITS.get(t, 0))
            j = i + 1
            if t in TENS and j < len(tokens) and tokens[j] in UNITS and UNITS[tokens[j]] < 10 \
                    and tokens[j] not in ("a", "an"):
                n += UNITS[tokens[j]]
                j += 1
            if j < len(tokens) and tokens[j] == "hundred":
                n *= 100
                j += 1
                if j < len(tokens) and tokens[j] == "and":
                    j += 1
                if j < len(tokens) and tokens[j] in TENS:
                    n += TENS[tokens[j]]
                    j += 1
                if j < len(tokens) and tokens[j] in UNITS and tokens[j] not in ("a", "an", "couple", "few"):
                    n += UNITS[tokens[j]]
                    j += 1
            out.append(str(n))
            i = j
        else:
            out.append(t)
            i += 1
    s = " ".join(out)
    return re.sub(r"\s+([,.!?;:])", r"\1", s)


FR_UNITS = {"zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
            "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14,
            "quinze": 15, "seize": 16}
FR_TENS = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60}


def fr_words_to_numbers(text):
    """'mets un minuteur de vingt-cinq minutes' -> '... de 25 minutes'."""
    toks = re.findall(r"\d+[:.h]\d+|[\w'àâçéèêëîïôûùüÿœ]+(?:-[\w'àâçéèêëîïôûùüÿœ]+)*|[^\w\s]", text.lower())
    out = []
    for t in toks:
        parts = [p for p in re.split(r"[- ]", t) if p and p != "et"]
        if parts and all(p in FR_UNITS or p in FR_TENS or p in ("cent", "cents", "vingts") for p in parts) \
                and not (len(parts) == 1 and parts[0] in ("un", "une")):
            n, cur = 0, 0
            for p in parts:
                if p in FR_TENS:
                    cur += FR_TENS[p]
                elif p in ("vingt", "vingts"):
                    cur = cur * 20 if cur else 20
                elif p in ("cent", "cents"):
                    cur = (cur or 1) * 100
                else:
                    v = FR_UNITS[p]
                    # quatre-vingt, soixante-dix
                    cur += v
            # "quatre vingt" written as units: 4 then 20 -> 80
            if "vingts" in parts or ("quatre" in parts and "vingt" in parts):
                cur = 80 + sum(FR_UNITS.get(p, 0) for p in parts[parts.index("vingt" if "vingt" in parts else "vingts") + 1:])
            out.append(str(n + cur))
        else:
            out.append(t)
    s = " ".join(out)
    return re.sub(r"\s+([,.!?;:])", r"\1", s)


def parse_duration_fr(text):
    s = fr_words_to_numbers(text)
    s = s.replace("une demi-heure", "30 minutes").replace("une demi heure", "30 minutes") \
         .replace("trois quarts d'heure", "45 minutes").replace("un quart d'heure", "15 minutes")
    total, found = 0.0, False
    m = re.search(r"\b(\d+)\s*h\s*(\d{1,2})\b", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    for m in re.finditer(r"(\d+(?:[.,]\d+)?|une?)\s*(secondes?|sec|minutes?|min|heures?|h|jours?)\b(\s+et\s+demie?)?", s):
        num = m.group(1)
        n = 1.0 if num in ("un", "une") else float(num.replace(",", "."))
        unit = m.group(2)
        sec = 1 if unit.startswith("sec") else 60 if unit.startswith("min") else 86400 if unit.startswith("jour") else 3600
        if m.group(3):
            n += 0.5
        total += n * sec
        found = True
    return total if found else None


def parse_duration(text):
    """Seconds in a phrase like '1 hour and 30 minutes', 'an hour and a half',
    '2 and a half minutes', '90 seconds'. None if there is none."""
    s = words_to_numbers(text)
    s = s.replace("half an hour", "30 minutes").replace("a quarter of an hour", "15 minutes") \
         .replace("quarter of an hour", "15 minutes")
    total = 0.0
    found = False
    for m in re.finditer(r"(\d+(?:[.,]\d+)?|a|an)\s*(?:and a half\s*)?"
                         r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|s|h)\b(\s+and a half)?", s):
        num = m.group(1)
        n = 1.0 if num in ("a", "an") else float(num.replace(",", "."))
        unit = m.group(2).rstrip("s") if m.group(2) not in ("s", "secs", "mins", "hrs") else m.group(2)
        sec = UNIT_SECONDS.get(unit, UNIT_SECONDS.get(m.group(2), 60))
        if "and a half" in m.group(0):
            n += 0.5
        total += n * sec
        found = True
    return total if found else None


def tz(settings):
    name = settings["assistant"].get("timezone") if settings else ""
    if name and ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.datetime.now().astimezone().tzinfo


def now(settings=None):
    return dt.datetime.now(tz(settings))


def parse_clock(text, settings=None):
    """'7 am', '7:30', '19h15', 'half past 7', 'noon' -> next datetime at that time."""
    s = words_to_numbers(text)
    base = now(settings)
    h = mnt = None
    if "noon" in s:
        h, mnt = 12, 0
    elif "midnight" in s:
        h, mnt = 0, 0
    m = re.search(r"half past (\d{1,2})", s)
    if m:
        h, mnt = int(m.group(1)), 30
    m2 = re.search(r"quarter past (\d{1,2})", s)
    if m2:
        h, mnt = int(m2.group(1)), 15
    m3 = re.search(r"quarter to (\d{1,2})", s)
    if m3:
        h, mnt = (int(m3.group(1)) - 1) % 24, 45
    if h is None:
        m = re.search(r"\b(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|o'clock)?", s)
        if not m:
            return None
        h, mnt = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(".", "")
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
    if not (0 <= h < 24 and 0 <= mnt < 60):
        return None
    t = base.replace(hour=h, minute=mnt, second=0, microsecond=0)
    if t <= base:
        # "7" in the evening means the next 7, possibly pm
        if h < 12 and "am" not in s and t + dt.timedelta(hours=12) > base and \
                not re.search(r"\b(am|morning)\b", s):
            t += dt.timedelta(hours=12)
        else:
            t += dt.timedelta(days=1)
    if "tomorrow" in s and t.date() == base.date():
        t += dt.timedelta(days=1)
    return t


def say_duration(seconds, lang="en"):
    seconds = int(round(seconds))
    if lang == "fr":
        if seconds < 60:
            return "%d seconde%s" % (seconds, "" if seconds <= 1 else "s")
        h, rem = divmod(seconds, 3600)
        m, s_ = divmod(rem, 60)
        parts = []
        if h:
            parts.append("%d heure%s" % (h, "" if h == 1 else "s"))
        if m:
            parts.append("%d minute%s" % (m, "" if m == 1 else "s"))
        if s_ and not h:
            parts.append("%d seconde%s" % (s_, "" if s_ == 1 else "s"))
        return " et ".join(parts)
    if seconds < 60:
        return "%d second%s" % (seconds, "" if seconds == 1 else "s")
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append("%d hour%s" % (h, "" if h == 1 else "s"))
    if m:
        parts.append("%d minute%s" % (m, "" if m == 1 else "s"))
    if s and not h:
        parts.append("%d second%s" % (s, "" if s == 1 else "s"))
    return " and ".join(parts)


def say_time(t, lang="en"):
    h, m = t.hour, t.minute
    if lang == "fr":
        return "%d heure%s%s" % (h, "" if h <= 1 else "s", " %d" % m if m else "")
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return "%d:%02d %s" % (h12, m, suffix) if m else "%d %s" % (h12, suffix)


FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]


def say_date(t, lang="en"):
    if lang == "fr":
        return "%s %d %s" % (FR_DAYS[t.weekday()], t.day, FR_MONTHS[t.month - 1])
    return t.strftime("%A, %B ") + str(t.day)
