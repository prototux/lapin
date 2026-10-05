"""Web search through OpenSERP (self-hosted metasearch), and page reading."""

import html
import re

import requests

from . import tool


def _cfg(app):
    return app.settings["search"]


def configured(app):
    return bool(_cfg(app).get("url"))


@tool("Search the web for current information: news, facts, opening hours, prices, events... Returns "
      "titles, links and snippets; read_page gets more detail from a result.",
      {"query": ("string", "search terms, in the language of the expected results"),
       "language": ("string", "results language code, e.g. 'fr' or 'en'")}, ["query"], available=configured)
def web_search(ctx, query, language=""):
    c = _cfg(ctx.app)
    errors = []
    for engine in c.get("engines", ["duck"]):
        params = {"text": query, "limit": c.get("results", 5)}
        if language:
            params["lang"] = language.upper()
        try:
            r = requests.get("%s/%s/search" % (c["url"].rstrip("/"), engine), params=params, timeout=15)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            errors.append("%s: %s" % (engine, e))
            continue
        results = [x for x in data.get("results", []) if x.get("title")]
        if not results:
            errors.append("%s: %s" % (engine, data.get("message", "no results")))
            continue
        return {"engine": engine, "results": [
            {"title": x["title"], "url": x.get("url", ""), "snippet": (x.get("snippet") or "")[:400]}
            for x in results[:c.get("results", 5)]]}
    return {"error": "search failed (%s)" % "; ".join(errors)[:300]}


@tool("Read the text of a web page (e.g. a search result) to answer in detail.",
      {"url": ("string", "http(s) URL")}, ["url"], available=configured)
def read_page(ctx, url):
    if not url.startswith(("http://", "https://")):
        return {"error": "only http(s) URLs"}
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (home assistant)"})
    except requests.RequestException as e:
        return {"error": str(e)[:200]}
    text = r.text
    text = re.sub(r"(?is)<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return {"url": url, "text": text[:3500]}


EXAMPLES = {"en": ["Search the web for the opening hours of the Louvre", "What's the latest news about the Mars mission?",
                   "Who won the match last night?", "How much does a Raspberry Pi 5 cost?"],
            "fr": ["Cherche sur internet les horaires du Louvre", "Quelles sont les dernières nouvelles sur la mission Mars ?",
                   "Qui a gagné le match hier soir ?", "Combien coûte un Raspberry Pi 5 ?"]}
