"""Jellyfin: music on the satellites, the movie / TV show library (for
recommendations: genres, unwatched, ratings, recently added, next up), and
remote control of Jellyfin apps (TV, phone...). Each household member can
have their own account (People page), so "unwatched" is theirs."""

import hashlib
import random
import threading
import urllib.parse

import requests

from . import tool
from ..integrations import account, anyone_has

_auth = {}          # (url, username or key) -> (token, user_id)
_lock = threading.Lock()
TICKS = 10_000_000


def _available(app):
    return anyone_has(app, "jellyfin")


def _header(token=None):
    h = 'MediaBrowser Client="Home assistant", Device="Voice assistant", DeviceId="voice-assistant-server", ' \
        'Version="1.0"'
    if token:
        h += ', Token="%s"' % token
    return {"Authorization": h}


class Jellyfin:
    def __init__(self, acct):
        self.base = acct["url"].rstrip("/")
        key = (self.base, acct.get("api_key") or acct.get("username"))
        with _lock:
            cached = _auth.get(key)
        if cached:
            self.token, self.user_id = cached
            return
        if acct.get("api_key"):
            self.token = acct["api_key"]
            users = self.get("/Users")
            want = (acct.get("username") or "").lower()
            pick = [u for u in users if u["Name"].lower() == want] or \
                [u for u in users if u.get("Policy", {}).get("IsAdministrator")] or users
            self.user_id = pick[0]["Id"]
        else:
            r = requests.post(self.base + "/Users/AuthenticateByName", headers=_header(), timeout=10,
                              json={"Username": acct["username"], "Pw": acct["password"]})
            if r.status_code >= 400:
                raise RuntimeError("Jellyfin login failed (%s)" % r.status_code)
            d = r.json()
            self.token, self.user_id = d["AccessToken"], d["User"]["Id"]
        with _lock:
            _auth[key] = (self.token, self.user_id)

    def get(self, path, **params):
        r = requests.get(self.base + path, headers=_header(getattr(self, "token", None)), params=params, timeout=10)
        if r.status_code == 401:
            with _lock:
                _auth.clear()
        r.raise_for_status()
        return r.json() if r.content else {}

    def post(self, path, **params):
        r = requests.post(self.base + path, headers=_header(self.token), params=params, timeout=10)
        r.raise_for_status()
        return r.json() if r.content else {}

    def items(self, **params):
        params.setdefault("userId", self.user_id)
        params.setdefault("Recursive", "true")
        return self.get("/Items", **params).get("Items", [])

    def stream_headers(self):
        return "Authorization: %s\r\n" % _header(self.token)["Authorization"]

    def stream_url(self, item_id):
        q = {"UserId": self.user_id, "DeviceId": "voice-assistant-server",
             "Container": "opus,mp3,aac,m4a,flac,webma,webm,wav,ogg", "TranscodingContainer": "mp3",
             "TranscodingProtocol": "http", "AudioCodec": "mp3", "MaxStreamingBitrate": "320000"}
        return "%s/Audio/%s/universal?%s" % (self.base, item_id, urllib.parse.urlencode(q))


def _client(ctx):
    acct = account(ctx.app, ctx.user, "jellyfin")
    if not acct:
        raise RuntimeError("no Jellyfin account configured")
    return Jellyfin(acct)


def _errors(fn):
    def wrapper(ctx, **kw):
        try:
            return fn(ctx, **kw)
        except (requests.RequestException, RuntimeError, ValueError, KeyError) as e:
            return {"error": "Jellyfin: %s" % str(e)[:200]}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__wrapped__ = fn
    return wrapper


# ------------------------------------------------------------------ music

def _tracks(jf, query, kind):
    audio = {"IncludeItemTypes": "Audio", "Fields": "Genres"}
    if kind == "random" or (not query and kind in ("auto", "song")):
        return jf.items(SortBy="Random", Limit=60, **audio), "random songs"
    if kind == "genre":
        return jf.items(Genres=query, SortBy="Random", Limit=200, **audio), "genre %s" % query
    if kind == "playlist":
        for pl in jf.items(IncludeItemTypes="Playlist", searchTerm=query):
            return jf.get("/Playlists/%s/Items" % pl["Id"], userId=jf.user_id).get("Items", []), "playlist %s" % pl["Name"]
        return [], "no playlist %r" % query
    if kind in ("artist", "auto"):
        artists = jf.get("/Artists", searchTerm=query, userId=jf.user_id, Limit=3).get("Items", [])
        if artists and (kind == "artist" or artists[0]["Name"].lower() == query.lower()):
            a = artists[0]
            return jf.items(ArtistIds=a["Id"], SortBy="Album,ParentIndexNumber,IndexNumber", Limit=300,
                            **audio), "artist %s" % a["Name"]
    if kind in ("album", "auto"):
        albums = jf.items(IncludeItemTypes="MusicAlbum", searchTerm=query, Limit=3)
        if albums and (kind == "album" or not jf.items(searchTerm=query, Limit=1, **audio)):
            al = albums[0]
            return jf.items(ParentId=al["Id"], SortBy="ParentIndexNumber,IndexNumber", **audio), "album %s by %s" % (
                al["Name"], al.get("AlbumArtist", "?"))
    songs = jf.items(searchTerm=query, Limit=30, **audio)
    if not songs and kind == "auto":
        artists = jf.get("/Artists", searchTerm=query, userId=jf.user_id, Limit=1).get("Items", [])
        if artists:
            return jf.items(ArtistIds=artists[0]["Id"], Limit=300, **audio), "artist %s" % artists[0]["Name"]
    return songs, "songs matching %r" % query


@tool("Play music from Jellyfin on satellites: a song, an artist, an album, a playlist, a genre or "
      "random songs.",
      {"query": ("string", "what to play (empty for random)"),
       "kind": ("string", "what the query is", ["auto", "song", "artist", "album", "playlist", "genre", "random"]),
       "shuffle": ("boolean", "shuffle (default: yes for an artist, genre or random; album / playlist order otherwise)"),
       "target": ("string", "'here' (default), a room, a device or 'all'")},
      available=_available)
@_errors
def jellyfin_play_music(ctx, query="", kind="auto", shuffle=None, target="here"):
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    if not sessions:
        return {"error": "no satellite online for %s" % target}
    jf = _client(ctx)
    tracks, what = _tracks(jf, (query or "").strip(), kind or "auto")
    if not tracks:
        return {"error": "nothing found (%s)" % what}
    if shuffle or (shuffle is None and what.startswith(("artist", "genre", "random"))):
        random.shuffle(tracks)
    items = [{"title": t["Name"], "artist": t.get("AlbumArtist") or ", ".join(t.get("Artists", [])),
              "album": t.get("Album", ""), "url": jf.stream_url(t["Id"]), "headers": jf.stream_headers()}
             for t in tracks[:300]]
    ctx.app.media.play_items(items, sessions, what, "jellyfin")
    return {"ok": True, "playing": what, "tracks": len(items),
            "first": "%s - %s" % (items[0]["artist"], items[0]["title"]), "on": [s.name for s in sessions]}


# ------------------------------------------------------------------ library

GENRE_ALIASES = {  # English names the model may use -> other spellings (metadata can be localized)
    "comedy": ["comédie", "comedie"], "drama": ["drame"], "adventure": ["aventure"],
    "science fiction": ["science-fiction", "sci-fi", "science-fiction & fantastique"], "sci-fi": ["science-fiction"],
    "fantasy": ["fantastique", "fantasy"], "family": ["familial", "famille"], "horror": ["horreur", "épouvante"],
    "documentary": ["documentaire"], "war": ["guerre"], "music": ["musique"], "mystery": ["mystère"],
    "history": ["histoire"], "crime": ["crime", "policier"], "kids": ["enfants", "kids"],
    "animation": ["animation"], "romance": ["romance"], "thriller": ["thriller"], "action": ["action"],
    "western": ["western"], "reality": ["téléréalité"],
}


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("-", " ").strip()


def _resolve_genre(wanted, available):
    """The library's own genre name for what the user / model asked."""
    w = _norm(wanted)
    names = {_norm(g): g for g in available}
    if w in names:
        return names[w]
    cands = [w] + [_norm(a) for a in GENRE_ALIASES.get(w, [])]
    for en, alts in GENRE_ALIASES.items():
        if w in [_norm(a) for a in alts]:
            cands += [_norm(en)] + [_norm(a) for a in alts]
    for c in cands:
        for n, g in names.items():
            if c == n or c in n or n in c:
                return g
    return wanted

def _video_view(i):
    ud = i.get("UserData", {})
    v = {"name": i["Name"], "year": i.get("ProductionYear"), "genres": i.get("Genres", [])[:4],
         "rating": round(i["CommunityRating"], 1) if i.get("CommunityRating") else None,
         "overview": (i.get("Overview") or "")[:220]}
    if i.get("Type") == "Movie":
        v["minutes"] = round(i.get("RunTimeTicks", 0) / TICKS / 60) or None
        v["watched"] = bool(ud.get("Played"))
    elif i.get("Type") == "Series":
        v["unwatched_episodes"] = ud.get("UnplayedItemCount")
        v["status"] = i.get("Status")
    elif i.get("Type") == "Episode":
        v["series"] = i.get("SeriesName")
        v["episode"] = "S%02dE%02d" % (i.get("ParentIndexNumber") or 0, i.get("IndexNumber") or 0)
    return v


@tool("Browse the household's Jellyfin movies or TV shows, to recommend something to watch or answer "
      "'do we have...'. Filter by genre, unwatched, search terms; sort by random, rating, recently added.",
      {"kind": ("string", "what to list", ["movie", "series"]),
       "query": ("string", "title search (optional)"),
       "genre": ("string", "genre, e.g. Comedy, Science Fiction, Animation (optional)"),
       "unwatched_only": ("boolean", "only what this user has not watched"),
       "sort": ("string", "order", ["random", "rating", "recent", "name"]),
       "limit": ("integer", "how many (default 8, max 25)")},
      available=_available)
@_errors
def jellyfin_library(ctx, kind="movie", query="", genre="", unwatched_only=False, sort="random", limit=8):
    jf = _client(ctx)
    t = "Series" if kind == "series" else "Movie"
    params = {"IncludeItemTypes": t, "Fields": "Genres,Overview,CommunityRating,RunTimeTicks,ProductionYear,Status",
              "Limit": max(1, min(int(limit or 8), 25)), "EnableUserData": "true"}
    order = {"random": ("Random", "Ascending"), "rating": ("CommunityRating", "Descending"),
             "recent": ("DateCreated", "Descending"), "name": ("SortName", "Ascending")}.get(sort or "random")
    params["SortBy"], params["SortOrder"] = order
    if query:
        params["searchTerm"] = query
    if unwatched_only:
        params["Filters"] = "IsUnplayed"
    genres = [g["Name"] for g in jf.get("/Genres", userId=jf.user_id, IncludeItemTypes=t,
                                        Recursive="true").get("Items", [])]
    if genre:
        params["Genres"] = _resolve_genre(genre, genres)
    items = jf.items(**params)
    total = jf.get("/Items", userId=jf.user_id, IncludeItemTypes=t, Recursive="true", Limit=0).get("TotalRecordCount")
    return {"kind": kind, "total_in_library": total, "items": [_video_view(i) for i in items],
            "genres_available": genres[:40]}


@tool("What this user is in the middle of on Jellyfin: next episodes to watch and things to resume.",
      available=_available)
@_errors
def jellyfin_next_up(ctx):
    jf = _client(ctx)
    fields = "Overview,ProductionYear,Genres"
    nxt = jf.get("/Shows/NextUp", userId=jf.user_id, Limit=8, Fields=fields).get("Items", [])
    try:
        res = jf.get("/UserItems/Resume", userId=jf.user_id, Limit=6, Fields=fields).get("Items", [])
    except requests.RequestException:
        res = jf.get("/Users/%s/Items/Resume" % jf.user_id, Limit=6, Fields=fields).get("Items", [])
    return {"next_up": [_video_view(i) for i in nxt],
            "resume": [dict(_video_view(i), progress_pct=round(i.get("UserData", {}).get("PlayedPercentage") or 0))
                       for i in res]}


# ------------------------------------------------------------------ remote

@tool("Control a Jellyfin app on another screen (TV, phone, browser...): play a movie / episode / show "
      "on it, pause, resume, stop, next. Not for the music playing on the satellites (use media_control). Without a device, lists the apps that can be controlled.",
      {"action": ("string", "what to do", ["list", "play", "pause", "resume", "stop", "next"]),
       "device": ("string", "app / device name, e.g. 'TV' or 'living room'"),
       "query": ("string", "for play: title of a movie, show or episode")},
      ["action"], available=_available)
@_errors
def jellyfin_remote(ctx, action, device="", query=""):
    jf = _client(ctx)
    sessions = [s for s in jf.get("/Sessions", ControllableByUserId=jf.user_id)
                if s.get("SupportsRemoteControl") and s.get("DeviceId") != "voice-assistant-server"]
    view = [{"device": s.get("DeviceName"), "app": s.get("Client"),
             "playing": (s.get("NowPlayingItem") or {}).get("Name")} for s in sessions]
    if action == "list" or not sessions:
        return {"sessions": view} if sessions else {"error": "no Jellyfin app open that can be controlled"}
    want = (device or "").lower()
    pick = [s for s in sessions if want and (want in s.get("DeviceName", "").lower() or
                                             want in s.get("Client", "").lower())] or sessions[:1]
    s = pick[0]
    if action == "play":
        if not query:
            return {"error": "what should be played?"}
        found = jf.items(searchTerm=query, IncludeItemTypes="Movie,Series,Episode", Limit=5)
        if not found:
            return {"error": "nothing called %r in Jellyfin" % query}
        item = found[0]
        if item["Type"] == "Series":
            nxt = jf.get("/Shows/NextUp", userId=jf.user_id, SeriesId=item["Id"], Limit=1).get("Items", [])
            item = nxt[0] if nxt else (jf.items(ParentId=item["Id"], IncludeItemTypes="Episode", Limit=1) or [item])[0]
        jf.post("/Sessions/%s/Playing" % s["Id"], playCommand="PlayNow", itemIds=item["Id"])
        return {"ok": True, "playing": _video_view(item)["name"], "on": s.get("DeviceName")}
    cmd = {"pause": "Pause", "resume": "Unpause", "stop": "Stop", "next": "NextTrack"}[action]
    jf.post("/Sessions/%s/Playing/%s" % (s["Id"], cmd))
    return {"ok": True, "done": action, "on": s.get("DeviceName")}


EXAMPLES = {"en": ["Play some music from Jellyfin", "Play the album Random Access Memories", "What should we watch tonight?",
                   "Recommend a comedy we haven't seen", "What new movies did we add?", "Which shows am I watching?",
                   "Do we have Inception?", "Play Inception on the TV", "Pause the TV"],
            "fr": ["Mets de la musique depuis Jellyfin", "Qu'est-ce qu'on regarde ce soir ?",
                   "Propose-moi une comédie qu'on n'a pas vue", "Quels films ont été ajoutés récemment ?",
                   "Quelles séries je suis en train de regarder ?", "Est-ce qu'on a Amélie ?",
                   "Lance Kaamelott sur la télé", "Mets la télé en pause"]}
