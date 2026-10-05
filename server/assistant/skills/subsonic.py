"""Music from Navidrome (or any Subsonic-compatible server), played on the
satellites. Each household member can have their own account (People page);
the household account is used otherwise."""

import hashlib
import random
import re
import secrets
import time
import urllib.parse

import requests

from . import tool
from ..integrations import account, anyone_has


def _available(app):
    return anyone_has(app, "subsonic")


def _params(acct):
    salt = secrets.token_hex(6)
    return {"u": acct["username"], "t": hashlib.md5((acct["password"] + salt).encode()).hexdigest(), "s": salt,
            "v": "1.16.1", "c": "home-assistant", "f": "json"}


def _call(acct, method, **params):
    r = requests.get("%s/rest/%s" % (acct["url"].rstrip("/"), method), params={**_params(acct), **params},
                     timeout=10)
    data = r.json().get("subsonic-response", {})
    if data.get("status") != "ok":
        raise RuntimeError(data.get("error", {}).get("message", "Subsonic error"))
    return data


_ARTISTS = {}


def library_artists(app, user):
    """All artist names of the library (cached 10 minutes)."""
    acct = account(app, user, "subsonic")
    if not acct:
        return []
    key = (acct["url"], acct["username"])
    hit = _ARTISTS.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        idx = _call(acct, "getArtists").get("artists", {}).get("index", [])
        names = [a["name"] for i in idx for a in i.get("artist", [])]
    except (requests.RequestException, RuntimeError, ValueError):
        return hit[1] if hit else []
    _ARTISTS[key] = (time.time(), names)
    return names


def _stream_url(acct, song_id):
    q = dict(_params(acct), id=song_id)
    q.pop("f", None)
    return "%s/rest/stream?%s" % (acct["url"].rstrip("/"), urllib.parse.urlencode(q))


def _items(acct, songs):
    return [{"title": s.get("title", ""), "artist": s.get("artist", ""), "album": s.get("album", ""),
             "url": _stream_url(acct, s["id"])} for s in songs]


def _songs_for(acct, query, kind):
    """Songs matching a request, and a description of what was found."""
    if kind == "random" or (not query and kind in ("auto", "song")):
        songs = _call(acct, "getRandomSongs", size=50)["randomSongs"].get("song", [])
        return songs, "random songs"
    if kind == "genre":
        songs = _call(acct, "getSongsByGenre", genre=query, count=100).get("songsByGenre", {}).get("song", [])
        return songs, "genre %s" % query
    if kind == "playlist":
        for pl in _call(acct, "getPlaylists").get("playlists", {}).get("playlist", []):
            if query.lower() in pl["name"].lower():
                songs = _call(acct, "getPlaylist", id=pl["id"])["playlist"].get("entry", [])
                return songs, "playlist %s" % pl["name"]
        return [], "no playlist %r" % query
    res = _call(acct, "search3", query=query, artistCount=20, albumCount=3, songCount=20)["searchResult3"]
    artists, albums, songs = res.get("artist", []), res.get("album", []), res.get("song", [])
    # search ranks collaborations ("X & Y", no albums) first: the exact name,
    # else the artist with the most albums
    q = query.lower().strip()
    artists.sort(key=lambda a: (a["name"].lower().strip() != q and
                                re.sub(r"^the ", "", a["name"].lower()) != re.sub(r"^the ", "", q),
                                -a.get("albumCount", 0)))
    exact = bool(artists) and artists[0]["name"].lower().strip() in (q, "the " + q)
    if kind in ("artist", "auto") and artists and (kind == "artist" or not songs or exact):
        a = artists[0]
        out = []
        for al in _call(acct, "getArtist", id=a["id"])["artist"].get("album", [])[:10]:
            out += _call(acct, "getAlbum", id=al["id"])["album"].get("song", [])
        if out:
            return out, "artist %s" % a["name"]
    if kind in ("album", "auto") and albums and (kind == "album" or not songs):
        al = albums[0]
        return _call(acct, "getAlbum", id=al["id"])["album"].get("song", []), "album %s by %s" % (
            al["name"], al.get("artist", "?"))
    return songs, "songs matching %r" % query


@tool("Play music from the household's Navidrome / Subsonic library on satellites: a song, an artist, "
      "an album, a playlist, a genre, or random songs.",
      {"query": ("string", "what to play (empty for random)"),
       "kind": ("string", "what the query is", ["auto", "song", "artist", "album", "playlist", "genre", "random"]),
       "shuffle": ("boolean", "shuffle (default: yes for an artist, genre or random; album / playlist order otherwise)"),
       "target": ("string", "'here' (default), a room, a device or 'all'")},
      available=_available)
def subsonic_play(ctx, query="", kind="auto", shuffle=None, target="here"):
    acct = account(ctx.app, ctx.user, "subsonic")
    if not acct:
        return {"error": "no Navidrome / Subsonic account configured"}
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    if not sessions:
        return {"error": "no satellite online for %s" % target}
    try:
        songs, what = _songs_for(acct, (query or "").strip(), kind or "auto")
    except (requests.RequestException, RuntimeError, ValueError) as e:
        return {"error": "Navidrome: %s" % e}
    if not songs:
        return {"error": "nothing found (%s)" % what}
    if shuffle or (shuffle is None and what.startswith(("artist", "genre", "random"))):
        random.shuffle(songs)
    items = _items(acct, songs[:200])
    ctx.app.media.play_items(items, sessions, what, "subsonic")
    return {"ok": True, "playing": what, "tracks": len(items), "first": "%s - %s" % (items[0]["artist"], items[0]["title"]),
            "on": [s.name for s in sessions]}


@tool("Search the Navidrome / Subsonic music library (artists, albums, songs) without playing.",
      {"query": ("string", "search terms")}, ["query"], available=_available)
def subsonic_search(ctx, query):
    acct = account(ctx.app, ctx.user, "subsonic")
    if not acct:
        return {"error": "no Navidrome / Subsonic account configured"}
    try:
        res = _call(acct, "search3", query=query, artistCount=5, albumCount=5, songCount=8)["searchResult3"]
    except (requests.RequestException, RuntimeError, ValueError) as e:
        return {"error": "Navidrome: %s" % e}
    return {"artists": [a["name"] for a in res.get("artist", [])],
            "albums": ["%s (%s)" % (a["name"], a.get("artist", "?")) for a in res.get("album", [])],
            "songs": ["%s - %s" % (s.get("artist", "?"), s["title"]) for s in res.get("song", [])]}


EXAMPLES = {"en": ["Play Daft Punk", "Play the album Discovery", "Play my workout playlist", "Play some jazz",
                   "Play random music in the kitchen", "Do I have anything by Miles Davis?"],
            "fr": ["Mets du Daft Punk", "Joue l'album Discovery", "Mets ma playlist sport", "Mets du jazz",
                   "Mets de la musique au hasard dans la cuisine", "Est-ce que j'ai des chansons de Brel ?"]}
