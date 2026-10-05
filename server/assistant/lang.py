"""Languages: detection of the language of a request (French / English) and
the fixed phrases the assistant says without the LLM."""

import re

FR_WORDS = set("""le la les un une des du de et est sont pas ne je tu il elle nous vous ils elles mon ma mes ton ta tes
son sa ses ce cette ces qui que quoi quel quelle quels quelles est-ce pour avec dans sur sous chez au aux en y à où
comment combien pourquoi quand bonjour salut merci oui non stp svp s'il plaît mets met allume éteins arrête joue
baisse monte minuteur réveil rappelle-moi rappelle heure heures minutes secondes demain aujourd'hui hier temps fait
c'est ça moi toi lui leur très plus moins peux peut veux voudrais fais faire dis quelle
lance ouvre ouvrir allume allumer éteindre ferme appelle appeler envoie envoyer cherche chercher montre trouve
donne mets-moi joue-moi lance-moi chanson musique morceau album lampe torche appareil photo télé message
rappel itinéraire emmène va aller""".split())
EN_WORDS = set("""the a an of and is are not do does i you he she we they my your his her it this that these those
what which who whom how why when where for with in on at to from please thanks hello hi set turn play stop timer
alarm remind me tomorrow today weather can could would will what's it's""".split())


def detect(text, default="en", min_words=1):
    """'fr' or 'en' from the words of a transcript. Shorter than `min_words`
    ("Wait.", "Bye", "No"): too little to go on, `default`."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœæ'-]+", (text or "").lower())
    if len(words) < max(1, min_words):
        return default
    fr = sum(1 for w in words if w in FR_WORDS) + 2 * len(re.findall(r"[àâçéèêëîïôûùœ]", text.lower()))
    en = sum(1 for w in words if w in EN_WORDS)
    # close call (often a French sentence with an English name, transcribed
    # in "English"): keep the conversation's language
    if abs(fr - en) <= 1:
        return default
    return "fr" if fr > en else "en"


PHRASES = {
    "timer_set": {"en": "Timer set for {d}.", "fr": "Minuteur réglé sur {d}."},
    "timer_set_label": {"en": "{L} timer set for {d}.", "fr": "Minuteur « {l} » réglé sur {d}."},
    "timer_none": {"en": "There's no timer running.", "fr": "Aucun minuteur en cours."},
    "timer_cancelled": {"en": "Timer cancelled.", "fr": "Minuteur annulé."},
    "timers_cancelled": {"en": "{n} timers cancelled.", "fr": "{n} minuteurs annulés."},
    "timer_which": {"en": "Which one? {items}.", "fr": "Lequel ? {items}."},
    "timer_item": {"en": "the {x} timer", "fr": "le minuteur {x}"},
    "timers_empty": {"en": "You don't have any timers running.", "fr": "Vous n'avez aucun minuteur en cours."},
    "timer_left": {"en": "{d} left", "fr": "il reste {d}"},
    "timer_left_label": {"en": "{d} left on the {l} timer", "fr": "il reste {d} pour le minuteur {l}"},
    "time": {"en": "It's {t}.", "fr": "Il est {t}."},
    "date": {"en": "It's {d}.", "fr": "Nous sommes le {d}."},
    "sorry": {"en": "Sorry, {e}.", "fr": "Désolé, {e}."},
    "wont": {"en": "Okay, I won't.", "fr": "D'accord, je ne le fais pas."},
    "done": {"en": "Done.", "fr": "C'est fait."},
    "failed": {"en": "That didn't work: {e}.", "fr": "Ça n'a pas marché : {e}."},
    "too_fast": {"en": "You're going a bit fast for me, try again in a moment.",
                 "fr": "Doucement, réessayez dans un instant."},
    "no_brain": {"en": "Sorry, I can't reach my brain right now.",
                 "fr": "Désolé, je n'arrive pas à joindre mon cerveau pour le moment."},
    "timer_done": {"en": "Your timer is done.", "fr": "Votre minuteur est terminé."},
    "timer_done_label": {"en": "Your {l} timer is done.", "fr": "Le minuteur {l} est terminé."},
    "alarm": {"en": "It's {t}.", "fr": "Il est {t}."},
    "np_track": {"en": "This is {title} by {artist}.", "fr": "C'est {title}, de {artist}."},
    "np_title": {"en": "This is {title}.", "fr": "C'est {title}."},
    "np_radio": {"en": "This is {name}.", "fr": "C'est la radio {name}."},
    "np_none": {"en": "Nothing is playing.", "fr": "Rien n'est en cours de lecture."},
    "giveup": {"en": "Sorry, I don't understand.", "fr": "Désolé, je ne comprends pas."},
    "reminder": {"en": "Reminder: {x}.", "fr": "Rappel : {x}."},
}


def say(key, lang="en", **kw):
    p = PHRASES[key]
    return p.get(lang, p["en"]).format(**kw)
