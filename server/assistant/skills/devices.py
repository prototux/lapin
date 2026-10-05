"""Satellites: volume, intercom / announcements, what is where."""

from . import tool


@tool("List the voice satellites (speakers) of the home, their room and state.")
def list_devices(ctx):
    out = []
    for d in ctx.app.devices.all_known():
        out.append({"name": d["name"], "room": d["room"] or "", "online": d["online"],
                    "here": d["id"] == ctx.device_id, "volume": d.get("volume")})
    return {"devices": out}


@tool("Change the speaker volume of satellites.",
      {"level": ("integer", "absolute volume 0-100"), "change": ("integer", "relative change, e.g. 10 or -10"),
       "target": ("string", "'here' (default), a room or device name, or 'all'")})
def set_volume(ctx, level=None, change=None, target="here"):
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    if not sessions:
        return {"error": "no such satellite online: %s" % target}
    done = []
    for s in sessions:
        v = s.volume if s.volume is not None else 60
        nv = int(level) if level is not None else v + int(change or 0)
        nv = max(0, min(100, nv))
        s.set(volume=nv)
        done.append({"device": s.name, "volume": nv})
    return {"ok": True, "changed": done}


@tool("Speak a message out loud on other satellites (intercom / announcement), e.g. 'dinner is ready'.",
      {"message": ("string", "what to say, as it should be spoken"),
       "target": ("string", "a room or device name, or 'all' (default: all except here)")},
      ["message"], roles=("admin", "adult", "child"))
def announce(ctx, message, target="all"):
    sessions = ctx.app.devices.resolve(target or "all", ctx)
    if (target or "all") == "all":
        sessions = [s for s in sessions if s.id != ctx.device_id] or sessions
    if not sessions:
        return {"error": "no satellite to announce on"}
    ctx.app.router.announce(message, sessions, chime=True)
    return {"ok": True, "on": [s.name for s in sessions]}


@tool("Stop everything the satellites are playing (answers, music, alarms). Only when asked to stop.",
      {"target": ("string", "'here' (default), a room, a device or 'all'")})
def stop_audio(ctx, target="here"):
    sessions = ctx.app.devices.resolve(target or "here", ctx)
    for s in sessions:
        ctx.app.media.stop(s)
        s.stop_all()
    return {"ok": True, "stopped": [s.name for s in sessions]}


EXAMPLES = {"en": ["Louder", "Set the volume to 30", "Tell everyone dinner is ready", "Announce in the kitchen that I'm leaving", "Stop", "Which speakers are on?"],
            "fr": ["Plus fort", "Mets le volume à 30", "Dis à tout le monde que le dîner est prêt", "Annonce dans la cuisine que je pars", "Arrête", "Quelles enceintes sont allumées ?"]}
