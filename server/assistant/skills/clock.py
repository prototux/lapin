"""Time and date."""

from . import tool
from .. import timeparse as tp


@tool("Current local time and date.")
def get_time(ctx):
    t = tp.now(ctx.settings)
    return {"time": tp.say_time(t), "date": tp.say_date(t), "year": t.year, "iso": t.isoformat(timespec="minutes"),
            "timezone": str(t.tzinfo)}


EXAMPLES = {"en": ["What time is it?", "What's the date today?", "What day is it?"],
            "fr": ["Quelle heure est-il ?", "On est quel jour ?", "Quelle est la date aujourd'hui ?"]}
