"""Encyclopedic lookups (Wikipedia) for facts the model may not know or
should not guess."""

import requests

from . import tool

UA = {"User-Agent": "home-voice-assistant/1.0 (self-hosted)"}


@tool("Look something up on Wikipedia: people, places, history, science. Returns a short summary.",
      {"query": ("string", "subject to look up"), "language": ("string", "wiki language code, default 'en'")},
      ["query"])
def wikipedia(ctx, query, language="en"):
    lang = (language or "en")[:5]
    base = "https://%s.wikipedia.org" % lang
    r = requests.get(base + "/w/api.php", params={"action": "query", "list": "search", "srsearch": query,
                                                  "format": "json", "srlimit": 1}, headers=UA, timeout=8)
    hits = r.json().get("query", {}).get("search", [])
    if not hits:
        return {"error": "nothing found for %r" % query}
    title = hits[0]["title"]
    s = requests.get(base + "/api/rest_v1/page/summary/" + requests.utils.quote(title.replace(" ", "_")),
                     headers=UA, timeout=8).json()
    return {"title": s.get("title", title), "summary": (s.get("extract") or "")[:1500],
            "source": s.get("content_urls", {}).get("desktop", {}).get("page", "")}


EXAMPLES = {"en": ["Who was Ada Lovelace?", "How tall is the Eiffel Tower?", "Tell me about the Roman Empire", "What is a black hole?"],
            "fr": ["Qui était Marie Curie ?", "Quelle est la hauteur de la tour Eiffel ?", "Parle-moi de l'Empire romain", "C'est quoi un trou noir ?"]}
