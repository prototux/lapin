"""Internet radio on the satellites (multi-room capable)."""

from . import tool


@tool("List the configured radio stations.")
def list_stations(ctx):
    return {"stations": [s["name"] for s in ctx.settings["media"].get("stations", [])]}


@tool("Play a radio station (or a stream URL) on satellites. Several rooms play in sync.",
      {"station": ("string", "station name (see list_stations) or an http(s) stream URL"),
       "target": ("string", "'here' (default), a room or device name, or 'all'")}, ["station"])
def play_radio(ctx, station, target="here"):
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    if not sessions:
        return {"error": "no satellite online for %s" % target}
    st = ctx.app.media.find_station(station)
    if not st:
        return {"error": "unknown station %r" % station, "stations": [s["name"] for s in ctx.settings["media"]["stations"]]}
    ctx.app.media.play(st, sessions)
    return {"ok": True, "playing": st["name"], "on": [s.name for s in sessions]}


@tool("Stop the music / radio.", {"target": ("string", "'here' (default), a room, device or 'all'")})
def stop_media(ctx, target="here"):
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    stopped = [s.name for s in sessions if ctx.app.media.stop(s)]
    return {"ok": True, "stopped": stopped}


@tool("Control the music or radio playing on the satellites (Navidrome, Jellyfin music, radio): next or "
      "previous track ('suivante', 'skip'), pause, resume, stop.",
      {"action": ("string", "what to do", ["pause", "resume", "next", "previous", "stop"]),
       "target": ("string", "'here' (default), a room, device or 'all'")}, ["action"])
def media_control(ctx, action, target="here"):
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    done = [s.name for s in sessions if ctx.app.media.control(s, action)]
    if not done:
        return {"error": "nothing is playing" if action != "next" else "no next track"}
    return {"ok": True, "done": action, "on": done}


@tool("What is playing on the satellites right now: song title, artist, album, or the radio station "
      "('what's this song', 'c'est quoi cette musique', 'qui chante').")
def now_playing(ctx):
    return {"playing": ctx.app.media.status()}


EXAMPLES = {"en": ["Play FIP", "Play Groove Salad in the kitchen", "Play France Inter everywhere", "Stop the music", "What's playing?"],
            "fr": ["Mets FIP", "Joue France Inter dans la cuisine", "Mets la radio partout", "Arrête la musique", "Qu'est-ce qui joue ?"]}
