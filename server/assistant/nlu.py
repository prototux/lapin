"""Fast-path NLU: a small deterministic grammar for the frequent commands
(stop, volume, timers, time, date, radio, yes / no). Answers in a few
milliseconds and keeps working when the LLM is down. Anything else goes to
the LLM agent."""

import difflib
import re

from . import timeparse as tp
from .lang import say

YES = r"((yes|yeah|yep|yup|sure|of course|do it|go ahead|please do|confirm(ed)?|ok(ay)?|absolutely|affirmative|correct|right|thanks|thank you)\s*)+"
NO = r"((no|nope|nah|don'?t|do not|cancel( it)?|thanks|thank you|never mind|nevermind|stop|negative|not now)\s*)+"


class Intent:
    def __init__(self, name, **slots):
        self.name = name
        self.slots = slots

    def __repr__(self):
        return "Intent(%s, %s)" % (self.name, self.slots)


def normalize(text):
    t = tp.words_to_numbers(text.lower())
    t = re.sub(r"[^\w\s'%:.-]", " ", t)
    t = re.sub(r"\b(please|hey|um+|uh+|so|okay so|could you|can you|would you)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .")


YES_FR = r"((oui|ouais|d'accord|ok|okay|vas-y|vas y|allez-y|bien sûr|confirme|c'est ça|exactement|absolument|volontiers|merci|fais-le|je confirme)\s*)+"
NO_FR = r"((non|surtout pas|annule|laisse tomber|pas maintenant|arrête|stop|merci|ne le fais pas)\s*)+"


def normalize_fr(text):
    t = tp.fr_words_to_numbers(text.lower())
    t = re.sub(r"[^\w\s'%:.àâçéèêëîïôûùüÿœ-]", " ", t)
    t = re.sub(r"\b(s'il te plaît|s'il vous plaît|stp|svp|euh+|bon|alors|est-ce que tu peux|tu peux|peux-tu|"
               r"pourrais-tu|tu pourrais|dis|hé|eh)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .")


def parse_fr(text, stations=(), pending=False):
    t = normalize_fr(text)
    if not t:
        return None
    if pending:
        if re.fullmatch(YES_FR, t):
            return Intent("confirm")
        if re.fullmatch(NO_FR, t):
            return Intent("deny")
    if re.fullmatch(r"(stop|arrête|arrête-toi|arrête ça|tais-toi|silence|chut|ça suffit|c'est bon|laisse tomber|"
                    r"annule|merci c'est tout|c'est tout)( merci)?", t):
        return Intent("stop")
    if re.fullmatch(r"(arrête|coupe|stoppe|éteins) (la |le )?(musique|radio|son|lecture)", t):
        return Intent("media_stop")
    if re.fullmatch(r"(mets en pause|pause)( la musique| la radio| la lecture)?|mets (la musique |la radio )?en pause", t):
        return Intent("media_control", action="pause")
    if re.fullmatch(r"(reprends|reprend|relance|continue|remets)( la musique| la radio| la lecture)?", t):
        return Intent("media_control", action="resume")
    if re.fullmatch(r"(suivant|chanson suivante|morceau suivant|piste suivante|passe à la suivante|"
                    r"la suivante|change de chanson|passe)", t):
        return Intent("media_control", action="next")
    if re.fullmatch(r"(précédent|chanson précédente|morceau précédent|piste précédente|la précédente|"
                    r"reviens en arrière)", t):
        return Intent("media_control", action="previous")
    m = re.fullmatch(r"(mets |règle |monte |baisse )?(le )?volume (à |a |sur )?(\d{1,3})( pour ?cent| %)?", t)
    if m:
        return Intent("volume_set", level=int(m.group(4)))
    if re.fullmatch(r"(plus fort|monte (le son|le volume)|augmente (le son|le volume)|(un peu )?plus fort)", t):
        return Intent("volume_change", change=15)
    if re.fullmatch(r"(moins fort|baisse (le son|le volume)|diminue (le son|le volume)|(un peu )?moins fort|"
                    r"parle moins fort)", t):
        return Intent("volume_change", change=-15)
    if re.search(r"\b(minuteur|minuteurs|timer|compte à rebours|chrono)\b", t):
        if re.search(r"\b(annule|arrête|supprime|enlève|stoppe)\b", t):
            m = re.search(r"minuteur (?:des |du |de la |de |pour (?:les |le |la )?)?([a-zàâçéèêëîïôûùüÿœ][\w àâçéèêëîïôûùüÿœ]*)$", t)
            return Intent("timer_cancel", label=m.group(1) if m else "", all=bool(re.search(r"\btous\b", t)))
        if re.search(r"combien|reste|restant", t):
            return Intent("timer_query")
        secs = tp.parse_duration_fr(t)
        if secs:
            m = re.search(r"\bpour (?:les |le |la |l'|mes |mon |ma )?([a-zàâçéèêëîïôûùüÿœ][\w àâçéèêëîïôûùüÿœ']*)$", t)
            label = m.group(1).strip() if m and not re.search(r"\b(minutes?|heures?|secondes?)\b", m.group(1)) else ""
            return Intent("timer_set", seconds=int(secs), label=label)
    if re.fullmatch(r"(quelle heure est-il|quelle heure il est|il est quelle heure|tu as l'heure|l'heure|"
                    r"quelle heure est il)( maintenant)?", t):
        return Intent("time")
    if re.fullmatch(r"(quel jour (sommes-nous|on est|est-on|sommes nous)|on est quel jour|quelle est la date|"
                    r"quelle date (sommes-nous|on est)|c'est quoi la date)( aujourd'hui)?", t):
        return Intent("date")
    m = re.fullmatch(r"(?:mets|joue|lance|allume) (?:la radio |la station )?(.+?)(?: (?:dans|sur) (?:la |le |l')?(.+))?", t)
    if m and stations:
        names = {st.lower(): st for st in stations}
        hit = difflib.get_close_matches(m.group(1), list(names), n=1, cutoff=0.6)
        if hit:
            return Intent("play_radio", station=names[hit[0]], target=m.group(2) or "here")
    if re.fullmatch(r"(?:mets|joue|lance)(?: (?:de la|un peu de|une))? (?:musique|playlist|chanson)(?: au hasard)?"
                    r"(?: (?:dans|sur) (?:la |le |l')?(.+))?", t):
        m = re.search(r"(?:dans|sur) (?:la |le |l')?(.+)$", t)
        return Intent("play_music", query="", kind="random", target=m.group(1) if m else "here")
    m = re.fullmatch(r"(?:mets|joue|lance)(?: moi)? (?:(du|de la|des|de l'|le|la|les|l'|un|une) ?)?(.+?)"
                     r"(?: (?:dans|sur) (?:la |le |l')?(salon|cuisine|chambre|bureau|salle de bain|entrée|garage|"
                     r"partout|toute la maison|maison))?", t)
    if m and not re.search(r"\b(minuteur|réveil|alarme|rappel|volume|son|pause|lumière|chauffage|télé|film|série)\b", t):
        return Intent("play_music", query=m.group(2), kind="auto", target=m.group(3) or "here")
    return None


# "mets du X" as speech recognition writes it when X is an English name:
# "met the X", "mais du X", "may do X", "make the X"...
GARBLED_PLAY = re.compile(r"(?:mets|met|mes|mais|mai|mette|may|make|made|mate|joue|joues|jou|lance|lands|play|"
                          r"mets moi|met moi|mais moi|mémoire|mémo|même|joue moi|jou moi|jou moins|joue moins|"
                          r"je moins|je vois|jeu moi)\s+"
                          r"(?:plutôt\s+)?(?:du|de la|de l'|des|de|d'|the|do|due|dew|you|le|la|les|l')\s*(.+)")
NOT_MUSIC = re.compile(r"\b(minuteur|timer|réveil|alarm|alarme|rappel|reminder|volume|son|sound|lumière|light|lights|"
                       r"chauffage|heating|télé|tv|film|movie|série|show|pause)\b")


def garbled_play(text):
    t = re.sub(r"[^\w\s']", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    m = GARBLED_PLAY.fullmatch(t)
    if m and not NOT_MUSIC.search(t):
        q, target = m.group(1), "here"
        r = re.search(r"\s+(?:dans|in|on|sur) (?:le |la |l'|the )?(salon|cuisine|chambre|bureau|salle de bain|"
                      r"garage|living room|kitchen|bedroom|office|bathroom|partout|everywhere)$", q)
        if r:
            q, target = q[:r.start()], r.group(1)
        q = re.sub(r"\s+(plutôt|instead|s'il te plaît|s'il vous plaît|stp|please|maintenant|now)$", "", q).strip()
        if q:
            # a guess: played only if it names something in the library
            return Intent("play_music", query=q, kind="auto", target=target, strict=True)
    return None


NOW_PLAYING = re.compile(
    r"\b(qu'est-ce qui (joue|passe)|qu'est-ce que (tu joues|tu passes|c'est que cette (musique|chanson))|"
    r"c'est quoi (cette|la|ce) (musique|chanson|morceau|titre|son)|tu joues quoi|qui chante|"
    r"quel(le)? est (cette|la|le|ce) (musique|chanson|morceau|titre|artiste|groupe)|"
    r"c'est (qui|quel|quelle) (qui chante|l'artiste|le groupe|chanson|morceau|artiste|groupe|titre)|"
    r"ça s'appelle comment|comment s'appelle (cette|la|ce) (chanson|musique|morceau)|"
    r"what('s| is) (playing|this song|this track|this music|that song|the song)|what song is (this|that|playing)|"
    r"who('s| is) (singing|this)|who sings (this|that)|what am i listening to)\b")
NEXT = re.compile(r"\b(suivant|suivante|next|skip|zappe|passe à la suivante)\b")
PREV = re.compile(r"\b(précédent|précédente|previous|reviens en arrière|go back)\b")
NOT_TRACK = re.compile(r"\b(épisode|episode|série|show|film|movie|minuteur|timer|alarme|alarm|semaine|week|"
                       r"mois|month|jour|day|année|year)\b")


def parse_media(text):
    """Media commands in either language (speech recognition mixes them:
    "Music suivante")."""
    t = re.sub(r"[^\w\s'-]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    if NOW_PLAYING.search(t):
        return Intent("now_playing")
    if len(t.split()) <= 5 and not NOT_TRACK.search(t):
        if NEXT.search(t):
            return Intent("media_control", action="next")
        if PREV.search(t):
            return Intent("media_control", action="previous")
    return None


def parse(text, stations=(), pending=False, lang="en"):
    if not pending:
        m = parse_media(text)
        if m:
            return m
    if lang == "fr":
        return parse_fr(text, stations, pending) or (None if pending else garbled_play(text))
    t = normalize(text)
    if not t:
        return None
    if pending:
        if re.fullmatch(YES, t):
            return Intent("confirm")
        if re.fullmatch(NO, t):
            return Intent("deny")
    if re.fullmatch(r"(stop|cancel|never ?mind|shut up|be quiet|quiet|silence|enough|that'?s enough|"
                    r"stop it|stop that|stop talking|thank you that'?s all|that'?s all)( thanks| thank you)?", t):
        return Intent("stop")
    if re.fullmatch(r"(stop|turn off|kill) (the )?(music|radio|song|stream|playback)", t):
        return Intent("media_stop")
    if re.fullmatch(r"pause( the)?( music| radio| song| playback)?|pause it", t):
        return Intent("media_control", action="pause")
    if re.fullmatch(r"(resume|continue|unpause|play again|keep playing)( the)?( music| radio| song| playback)?", t):
        return Intent("media_control", action="resume")
    if re.fullmatch(r"(next|skip)( song| track| one| this( song| track)?)?|play the next (song|track)", t):
        return Intent("media_control", action="next")
    if re.fullmatch(r"(previous|go back|last)( song| track| one)?|play the previous (song|track)", t):
        return Intent("media_control", action="previous")

    m = re.fullmatch(r"(set |turn |change )?(the )?volume (to |at )?(\d{1,3})( percent| %)?", t) or \
        re.fullmatch(r"volume (\d{1,3})", t)
    if m:
        return Intent("volume_set", level=int(m.groups()[-2] if len(m.groups()) > 1 else m.group(1)))
    if re.fullmatch(r"(turn (it |the volume |the sound )?up|louder|volume up|(increase|raise) the (volume|sound)|"
                    r"(a bit |a little )?louder( please)?)", t):
        return Intent("volume_change", change=15)
    if re.fullmatch(r"(turn (it |the volume |the sound )?down|quieter|softer|volume down|(decrease|lower) the "
                    r"(volume|sound)|(a bit |a little )?(quieter|softer))", t):
        return Intent("volume_change", change=-15)

    if re.search(r"\btimers?\b|\bcountdown\b", t):
        if re.search(r"\b(cancel|stop|delete|remove|clear)\b", t):
            m = re.search(r"(?:cancel|stop|delete|remove|clear) (?:the |my |all )?(?:(\w[\w ]*?) )?timers?", t)
            label = (m.group(1) or "") if m else ""
            label = "" if label in ("all", "all the", "all my", "the") else label
            return Intent("timer_cancel", label=label, all=bool(re.search(r"\ball\b", t)))
        if re.search(r"how (much|long)|time left|remaining|left on", t):
            return Intent("timer_query")
        secs = tp.parse_duration(t)
        if secs:
            label = ""
            m = re.search(r"\b(?:called|named|labell?ed)\s+([\w ]+)$", t)
            m2 = re.search(r"\bfor (?:the |my |a |an )?([a-z][a-z ]*)$", t)
            m3 = re.search(r"\b([a-z]+) timer\b", t)
            if m:
                label = m.group(1)
            elif m2 and not re.search(r"\b(hours?|minutes?|seconds?|half)\b", m2.group(1)):
                label = m2.group(1)
            elif m3 and m3.group(1) not in ("a", "an", "the", "my", "set", "start", "new", "one", "minute",
                                            "minutes", "second", "seconds", "hour", "hours", "half"):
                label = m3.group(1)
            return Intent("timer_set", seconds=int(secs), label=label.strip())
    if re.fullmatch(r"(what time is it|what'?s the time|what is the time|tell me the time|time|"
                    r"what time is it now|do you have the time)( now| right now)?", t):
        return Intent("time")
    if re.fullmatch(r"(what'?s|what is) (the |today'?s )?date( today)?|what day is (it|today)( today)?|"
                    r"what'?s today'?s date", t):
        return Intent("date")

    m = re.fullmatch(r"play (?:the )?(?:radio )?(.+?)(?: radio)?(?: (?:in|on) (?:the )?(.+))?", t)
    if m and stations:
        name = m.group(1)
        names = {s.lower(): s for s in stations}
        hit = difflib.get_close_matches(name, list(names), n=1, cutoff=0.6)
        if hit:
            return Intent("play_radio", station=names[hit[0]], target=m.group(2) or "here")
    if re.fullmatch(r"play (?:some |me some )?(?:music|songs?|something)(?: at random)?(?: (?:in|on) (?:the )?(.+))?", t):
        m = re.search(r"(?:in|on) (?:the )?(.+)$", t)
        return Intent("play_music", query="", kind="random", target=m.group(1) if m else "here")
    m = re.fullmatch(r"play (?:some |me some |me )?(.+?)(?: (?:in|on) (?:the )?(living room|kitchen|bedroom|office|"
                     r"bathroom|garage|everywhere|whole house|house))?", t)
    if m and not re.search(r"\b(timer|alarm|movie|film|show|episode|tv|video)\b", t):
        return Intent("play_music", query=m.group(1), kind="auto", target=m.group(2) or "here")
    return garbled_play(text)


def _library_name(reg, ctx, names, query):
    """The artist of the music library that `query` (as heard) names, or None:
    "ultravomide" -> "Ultra Vomit", "baby métal" -> "BABYMETAL"."""
    import difflib
    import unicodedata
    from .skills.subsonic import library_artists
    if "subsonic_play" not in names:
        return None

    def key(x):
        x = unicodedata.normalize("NFKD", x.lower()).encode("ascii", "ignore").decode()
        x = re.sub(r"^(the|les|le|la) ", "", x)
        return re.sub(r"[^a-z0-9]", "", x)
    q = key(query)
    if len(q) < 3:
        return None
    best, best_r = None, 0.0
    for a in library_artists(ctx.app, ctx.user):
        r = difflib.SequenceMatcher(None, q, key(a)).ratio()
        if r > best_r:
            best, best_r = a, r
    return best if best_r >= 0.78 else None


def execute(intent, ctx):
    """Runs an intent; returns the spoken reply (may be '') or None to fall
    back to the LLM."""
    app = ctx.app
    reg = app.skills
    n, s = intent.name, intent.slots
    if n == "stop":
        reg.call("stop_audio", {"target": "here"}, ctx)
        app.notifier.stop_ringing(ctx.device_id)
        ctx.silent = True
        ctx.ack = "done"
        return ""
    if n == "media_stop":
        reg.call("stop_media", {"target": "here"}, ctx)
        ctx.silent = True
        ctx.ack = "done"
        return ""
    if n == "media_control":
        r = reg.call("media_control", {"action": s["action"]}, ctx)
        own = reg.device_tools(ctx)
        if r.get("error") and "phone_media" in own:
            # nothing from the server here: the phone's own player (media keys)
            r = reg.call("phone_media", {"action": s["action"]}, ctx)
        if r.get("error"):
            return None             # let the model answer (e.g. nothing playing)
        ctx.silent = True
        ctx.ack = "done"
        return ""
    if n == "now_playing":
        L = getattr(ctx, "lang", "en")
        p = app.media.here(ctx)
        if not p and "phone_now_playing" in reg.device_tools(ctx):
            r = reg.call("phone_now_playing", {}, ctx)
            if r.get("title"):
                return say("np_track", L, title=r["title"], artist=r["artist"]) if r.get("artist") else \
                    say("np_title", L, title=r["title"])
            return None if r.get("error") else say("np_none", L)
        if not p:
            return say("np_none", L)
        if p.source == "radio":
            return say("np_radio", L, name=p.name)
        t = p.track_view()
        if not t["title"]:
            return None
        return say("np_track", L, title=t["title"], artist=t["artist"]) if t["artist"] else \
            say("np_title", L, title=t["title"])
    if n in ("volume_set", "volume_change"):
        args = {"level": s["level"]} if n == "volume_set" else {"change": s["change"]}
        r = reg.call("set_volume", args, ctx)
        if r.get("error"):
            return None
        ctx.silent = True       # the earcon and the LED ring say it
        return ""
    L = getattr(ctx, "lang", "en")
    if n == "timer_set":
        r = reg.call("set_timer", {"seconds": s["seconds"], "label": s.get("label", "")}, ctx)
        if r.get("error"):
            return say("sorry", L, e=r["error"])
        d = tp.say_duration(s["seconds"], L)
        lab = s.get("label")
        return say("timer_set_label", L, L=lab.capitalize(), l=lab, d=d) if lab else say("timer_set", L, d=d)
    if n == "timer_cancel":
        r = reg.call("cancel_timer", {"kind": "timer", "label": s.get("label", ""), "all": s.get("all", False)}, ctx)
        if r.get("error"):
            items = ", ".join(say("timer_item", L, x=i["label"] or i["remaining"]) for i in r["items"])
            return say("timer_which", L, items=items)
        c = r["cancelled"]
        return say("timer_none", L) if c == 0 else say("timer_cancelled", L) if c == 1 else say("timers_cancelled", L, n=c)
    if n == "timer_query":
        items = ctx.app.store.active_timers("timer")
        if not items:
            return say("timers_empty", L)
        import time as _t
        parts = [say("timer_left_label", L, d=tp.say_duration(i["due"] - _t.time(), L), l=i["label"]) if i["label"]
                 else say("timer_left", L, d=tp.say_duration(i["due"] - _t.time(), L)) for i in items]
        txt = "; ".join(parts)
        return txt[:1].upper() + txt[1:] + "."
    if n in ("time", "date"):
        t = tp.now(ctx.settings)
        return say("time", L, t=tp.say_time(t, L)) if n == "time" else say("date", L, d=tp.say_date(t, L))
    if n == "play_music":
        q, kind = s["query"].strip(), s["kind"]
        m = re.match(r"(?:the |my |ma |mon |l')?(playlist|album|artiste?|artist|genre)\s+(?:de |du |by )?(.+)", q)
        if m:
            kind = {"artiste": "artist", "artist": "artist"}.get(m.group(1), m.group(1))
            q = m.group(2)
        s = dict(s, query=q, kind=kind)
        local = reg.replaced(ctx).get("subsonic_play")
        names = [t.name for t in reg.usable(ctx)]
        if local and kind == "auto" and not s.get("strict"):
            # "lance l'appareil photo" is an app, not music: on a device with its
            # own player, the shortcut only takes names from the music library
            # (songs, apps and the rest go to the model, which tells them apart)
            s = dict(s, strict=True)
        if s.get("strict"):
            # a misheard request: only a name from the library (same Navidrome
            # library as the phone's player) is taken as music
            name = _library_name(reg, ctx, names + ["subsonic_play"] * bool(local), q)
            if not name:
                return None         # not a name from the library: the model decides
            s = dict(s, query=name)
        if local:
            # the device plays music in its own player (a phone's Subsonic app)
            r = reg.call(local, {"query": s["query"], "kind": s["kind"]}, ctx)
            if r.get("error"):
                return None
            ctx.silent = True
            ctx.ack = "done"
            return ""
        # the user's library: Navidrome first, then Jellyfin
        for tool_name in ("subsonic_play", "jellyfin_play_music"):
            if tool_name in names:
                r = reg.call(tool_name, {"query": s["query"], "kind": s["kind"], "target": s.get("target", "here")}, ctx)
                if not r.get("error"):
                    ctx.silent = True
                    ctx.ack = "done"
                    return ""
        return None                 # nothing found: the model gets a chance
    if n == "play_radio":
        r = reg.call("play_radio", {"station": s["station"], "target": s.get("target", "here")}, ctx)
        if r.get("error"):
            return None
        ctx.silent = True
        ctx.ack = "done"
        return ""
    return None
