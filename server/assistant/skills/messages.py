"""Send a text message to a household member on their messaging channel."""

from . import tool


def _available(app):
    return bool(app.messaging and app.messaging.can_send())


@tool("Send a text message to a household member (on Telegram or the web chat).",
      {"to": ("string", "user name"), "text": ("string", "the message")}, ["to", "text"],
      risk="confirm", available=_available, roles=("admin", "adult"),
      summary=lambda a: "send '%s' to %s" % (a.get("text", ""), a.get("to", "")))
def send_message(ctx, to, text):
    ok, detail = ctx.app.messaging.send_to_user(to, "%s (from %s)" % (text, ctx.user))
    return {"ok": ok, "detail": detail}


EXAMPLES = {"en": ["Send a message to Anna saying I'll be late", "Tell Paul on Telegram that dinner is at eight"],
            "fr": ["Envoie un message à Anna pour dire que je serai en retard", "Dis à Paul sur Telegram que le dîner est à 20 h"]}
