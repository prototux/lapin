"""Device tools: computer actions the server's language model can call
(PROTOCOL.md sections 5 and 6). The server adds "(on the user's computer)" to
each description."""

import logging

from . import apps, files, system

log = logging.getLogger("tools")


def _schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required)}


class Tool:
    def __init__(self, name, description, fn, parameters=None, silent=False, confirm=False, summary=""):
        self.name = name
        self.description = description
        self.fn = fn
        self.parameters = parameters or _schema()
        self.silent = silent
        self.confirm = confirm
        self.summary = summary

    def spec(self):
        d = {"name": self.name, "description": self.description, "parameters": self.parameters,
             "silent": self.silent, "confirm": self.confirm}
        if self.summary:
            d["summary"] = self.summary
        return d


class Registry:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.tools = {t.name: t for t in self._build()}

    def _build(self):
        return [
            Tool("open_app",
                 "Open (launch) an application installed on the computer, by name, e.g. Firefox, "
                 "Calculator, Terminal, Spotify, file manager. French or English names work. On failure "
                 "the result lists close candidates; offer them to the user.",
                 apps.open_app,
                 _schema({"app": {"type": "string", "description": "application name as the user said it"}},
                         ["app"]), silent=True),
            Tool("open_url",
                 "Open a web page or link in the default browser (or the matching app for mailto:, tel:...).",
                 files.open_url,
                 _schema({"url": {"type": "string", "description": "full URL, or a domain like wikipedia.org"}},
                         ["url"]), silent=True),
            Tool("open_folder",
                 "Open a folder in the file manager: documents, downloads, pictures, music, videos, desktop, "
                 "home, or the name of a folder in the home directory.",
                 files.open_folder,
                 _schema({"name": {"type": "string",
                                   "description": "documents, downloads, pictures, music, videos, desktop, home, "
                                                  "or a folder name / path"}}, ["name"]), silent=True),
            Tool("computer_media",
                 "Control the media player running on the computer (Spotify, VLC, a browser video...): play, "
                 "pause, next or previous track. Not for music the assistant itself plays.",
                 system.computer_media,
                 _schema({"action": {"type": "string", "enum": ["play", "pause", "next", "previous"]}},
                         ["action"]), silent=True),
            Tool("computer_volume",
                 "Set the computer's system sound volume: an absolute level (0-100), a relative change "
                 "(e.g. +10 or -10), or mute/unmute. Returns the new level.",
                 system.computer_volume,
                 _schema({"level": {"type": "integer", "minimum": 0, "maximum": 100,
                                    "description": "absolute volume in percent"},
                          "change": {"type": "integer", "description": "relative change in percent, e.g. 10 or -10"},
                          "mute": {"type": "boolean", "description": "true to mute, false to unmute"}}),
                 silent=True),
            Tool("lock_screen", "Lock the computer's screen (the session stays open).",
                 system.lock_screen, silent=True),
            Tool("screenshot",
                 "Take a screenshot of the whole screen and save it as a PNG in the Pictures folder. "
                 "Returns the file path.",
                 lambda: files.screenshot(self.cfg.get("screenshot_dir", "") if self.cfg else "")),
            Tool("copy_to_clipboard", "Copy a text to the computer's clipboard, ready to paste.",
                 files.copy_to_clipboard,
                 _schema({"text": {"type": "string", "description": "the exact text to copy"}}, ["text"]),
                 silent=True),
            Tool("read_clipboard",
                 "Read the text currently in the computer's clipboard (to read it out, summarize or "
                 "translate it). Long texts are truncated.",
                 files.read_clipboard),
            Tool("power",
                 "Shut down, reboot, suspend (sleep) the computer, or log the user out. Call it as soon as "
                 "the user asks: the confirmation question is handled automatically, don't ask it yourself.",
                 system.power,
                 _schema({"action": {"type": "string", "enum": ["shutdown", "reboot", "suspend", "logout"]}},
                         ["action"]),
                 confirm=True, summary="{action} the computer"),
            Tool("system_info",
                 "Information about the computer: battery level and charging state (if it has a battery), "
                 "uptime, free disk space, free memory, operating system.",
                 system.system_info),
        ]

    def specs(self):
        return [t.spec() for t in self.tools.values()]

    def call(self, name, args):
        """Runs a tool; always returns a short dict (errors as {"error": ...})."""
        t = self.tools.get(name)
        if not t:
            return {"error": "unknown tool %s" % name}
        args = args if isinstance(args, dict) else {}
        props = t.parameters.get("properties", {})
        kwargs = {k: v for k, v in args.items() if k in props}
        try:
            res = t.fn(**kwargs)
        except TypeError as e:
            res = {"error": "bad arguments: %s" % e}
        except Exception as e:
            log.exception("tool %s failed", name)
            res = {"error": str(e)[:300] or e.__class__.__name__}
        return res if isinstance(res, dict) else {"result": res}
