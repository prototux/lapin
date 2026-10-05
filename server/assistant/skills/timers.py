"""Timers, alarms and reminders (durable: stored, fired by the notifier)."""

import datetime as dt
import time

from . import tool
from .. import timeparse as tp


def _timer_view(t, settings):
    left = t["due"] - time.time()
    when = dt.datetime.fromtimestamp(t["due"], tp.tz(settings))
    return {"id": t["id"], "kind": t["kind"], "label": t["label"], "remaining": tp.say_duration(max(0, left)),
            "at": tp.say_time(when), "date": tp.say_date(when), "room_device": t["device_id"]}


@tool("Start a countdown timer.",
      {"seconds": ("integer", "duration in seconds"), "label": ("string", "what it is for, e.g. 'pasta'")},
      ["seconds"])
def set_timer(ctx, seconds, label=""):
    seconds = int(seconds)
    if seconds <= 0 or seconds > 86400 * 2:
        return {"error": "duration must be between 1 second and 48 hours"}
    tid = ctx.app.store.add_timer("timer", time.time() + seconds, label or "", seconds, ctx.device_id,
                                  ctx.user, ctx.channel, ctx.chat_id)
    ctx.app.notifier.reschedule()
    return {"ok": True, "id": tid, "duration": tp.say_duration(seconds), "label": label}


@tool("List the running timers, alarms and reminders with the time left.",
      {"kind": ("string", "filter", ["timer", "alarm", "reminder"])})
def list_timers(ctx, kind=None):
    items = ctx.app.store.active_timers(kind)
    return {"items": [_timer_view(t, ctx.settings) for t in items], "count": len(items)}


@tool("Cancel timers, alarms or reminders, by label, id, or all of a kind.",
      {"kind": ("string", "what to cancel", ["timer", "alarm", "reminder"]),
       "label": ("string", "label or part of it; empty with all=true cancels every one of the kind"),
       "id": ("integer", "exact id from list_timers"), "all": ("boolean", "cancel all of that kind")})
def cancel_timer(ctx, kind="timer", label="", id=None, all=False):
    items = ctx.app.store.active_timers(kind)
    if id is not None:
        items = [t for t in items if t["id"] == int(id)]
    elif label:
        items = [t for t in items if label.lower() in (t["label"] or "").lower()]
    elif not all and len(items) > 1:
        return {"error": "several %ss are running, ask which one" % kind,
                "items": [_timer_view(t, ctx.settings) for t in items]}
    for t in items:
        ctx.app.store.set_timer_status(t["id"], "cancelled")
    ctx.app.notifier.reschedule()
    return {"cancelled": len(items), "labels": [t["label"] for t in items]}


@tool("Set an alarm at a clock time (local time). For a relative delay use set_timer.",
      {"time": ("string", "time of day, e.g. '07:30' or '7:30 am'"),
       "date": ("string", "YYYY-MM-DD if not the next occurrence"),
       "label": ("string", "optional name"),
       "repeat": ("string", "repeat pattern", ["", "daily", "weekdays", "weekends"])},
      ["time"])
def set_alarm(ctx, time=None, date="", label="", repeat=""):
    t = tp.parse_clock(time or "", ctx.settings)
    if not t:
        return {"error": "could not understand the time %r" % time}
    if date:
        try:
            d = dt.date.fromisoformat(date)
            t = t.replace(year=d.year, month=d.month, day=d.day)
        except ValueError:
            pass
    tid = ctx.app.store.add_timer("alarm", t.timestamp(), label, 0, ctx.device_id, ctx.user, ctx.channel,
                                  ctx.chat_id, repeat or "")
    ctx.app.notifier.reschedule()
    return {"ok": True, "id": tid, "at": tp.say_time(t), "date": tp.say_date(t), "repeat": repeat}


@tool("Remind the user of something later, at a time or after a delay. The reminder is spoken on "
      "the device (or sent on the chat it was asked from).",
      {"text": ("string", "what to remind, phrased for the user, e.g. 'take the laundry out'"),
       "in_seconds": ("integer", "delay from now"),
       "at": ("string", "local date and time, ISO 'YYYY-MM-DDTHH:MM'")},
      ["text"])
def set_reminder(ctx, text, in_seconds=None, at=None):
    if in_seconds:
        due = time.time() + int(in_seconds)
    elif at:
        try:
            when = dt.datetime.fromisoformat(at)
            if when.tzinfo is None:
                when = when.replace(tzinfo=tp.tz(ctx.settings))
            due = when.timestamp()
        except ValueError:
            return {"error": "bad 'at' value, use YYYY-MM-DDTHH:MM"}
    else:
        return {"error": "give in_seconds or at"}
    if due <= time.time():
        return {"error": "that time is in the past"}
    tid = ctx.app.store.add_timer("reminder", due, text, 0, ctx.device_id, ctx.user, ctx.channel, ctx.chat_id)
    ctx.app.notifier.reschedule()
    when = dt.datetime.fromtimestamp(due, tp.tz(ctx.settings))
    return {"ok": True, "id": tid, "at": tp.say_time(when), "date": tp.say_date(when)}


EXAMPLES = {"en": ["Set a timer for 10 minutes", "Set a pasta timer for 8 minutes", "How much time is left?", "Cancel the timer", "Wake me up at 7:30 tomorrow", "Set an alarm at 6:45 on weekdays", "Remind me in 20 minutes to take the laundry out", "Remind me tomorrow at 9 to call the dentist", "What timers are running?"],
            "fr": ["Mets un minuteur de 10 minutes", "Minuteur de 8 minutes pour les pâtes", "Combien de temps reste-t-il ?", "Annule le minuteur", "Réveille-moi demain à 7 h 30", "Mets un réveil à 6 h 45 en semaine", "Rappelle-moi dans 20 minutes de sortir le linge", "Rappelle-moi demain à 9 h d'appeler le dentiste"]}
