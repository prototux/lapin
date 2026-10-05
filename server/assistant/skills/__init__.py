"""Skill / tool registry.

A skill module defines tools with the @tool decorator. Each tool has a JSON
schema (exposed to the LLM), a risk level and the roles allowed to use it:
    risk "safe"     runs directly
    risk "confirm"  needs an explicit "yes" from the user first (guardrails)
Handlers get a TurnContext and the arguments, and return a dict that goes
back to the LLM as JSON (keep it short and factual).
"""

import collections
import importlib
import inspect
import logging
import pkgutil
import time

log = logging.getLogger("skills")

ROLES = ("admin", "adult", "child", "guest")


class Tool:
    def __init__(self, fn, name, description, parameters, skill, risk="safe",
                 roles=ROLES, available=None, summary=None):
        self.fn = fn
        self.name = name
        self.description = description
        self.parameters = parameters
        self.skill = skill
        self.risk = risk
        self.roles = roles
        self.available = available        # fn(app) -> bool
        self.summary = summary            # fn(args) -> text for confirmations

    def spec(self):
        desc = self.description
        if self.risk == "confirm":
            # otherwise the model tends to ask "are you sure?" itself and never call
            # the tool, so nothing is pending when the user says yes
            desc += (" Call it as soon as the user asks: the system then asks the user to confirm "
                     "before it runs; don't ask for confirmation yourself.")
        return {"type": "function", "function": {"name": self.name, "description": desc,
                                                 "parameters": self.parameters}}


_TOOLS = {}
DESCRIPTIONS = {
    "timers": "Timers, alarms and reminders. Fast commands work without the language model.",
    "clock": "Time and date.", "weather": "Weather now and forecasts (Open-Meteo, home location in Settings).",
    "calc": "Exact arithmetic and unit conversions.", "memory": "Facts the household asks it to remember.",
    "devices": "Volume, intercom / announcements, what the satellites are doing.",
    "media": "Internet radio on one or several satellites, in sync.",
    "knowledge": "Wikipedia lookups for facts.",
    "homeassistant": "Lights, switches, climate, locks... through Home Assistant (URL and token in Settings).",
    "messages": "Send a message to someone on Telegram or the web chat (asks first).",
    "device": "Actions a phone or computer offers itself (messages, calls, navigation, apps...), only "
              "for requests made on that device. Risky ones ask first.",
}
EXAMPLES = {}       # skill name -> {"en": [...], "fr": [...]}


def tool(description, params=None, required=None, risk="safe", roles=ROLES, available=None, summary=None,
         name=None):
    """Declares a tool. params: {name: (type, description[, enum])}."""
    def deco(fn):
        props = {}
        for p, spec in (params or {}).items():
            t, d = spec[0], spec[1]
            props[p] = {"type": t, "description": d}
            if len(spec) > 2:
                props[p]["enum"] = spec[2]
        schema = {"type": "object", "properties": props, "required": list(required or [])}
        skill = fn.__module__.rsplit(".", 1)[-1]
        t = Tool(fn, name or fn.__name__, description, schema, skill, risk, roles, available, summary)
        _TOOLS[t.name] = t
        return fn
    return deco


def _device_call(session, name):
    def run(ctx, **args):
        return session.call_tool(name, args)
    return run


def _summary(d):
    """Text of the confirmation question: the device's template ("Send
    {message} to {contact}") filled with the arguments."""
    def fn(args):
        if d.get("summary"):
            try:
                return d["summary"].format_map(collections.defaultdict(str, args))
            except (ValueError, IndexError, AttributeError):
                pass
        return "%s %s" % (d["name"].replace("_", " "), ", ".join("%s: %s" % kv for kv in args.items()))
    return fn


class Registry:
    def __init__(self, app):
        self.app = app
        for m in pkgutil.iter_modules(__path__):
            try:
                mod = importlib.import_module("%s.%s" % (__name__, m.name))
                if hasattr(mod, "EXAMPLES"):
                    EXAMPLES[m.name] = mod.EXAMPLES
            except Exception:
                log.exception("skill module %s failed to load", m.name)
        self.tools = dict(_TOOLS)

    def skills(self):
        out = {}
        disabled = set(self.app.settings["skills"].get("disabled", []))
        for t in self.tools.values():
            s = out.setdefault(t.skill, {"name": t.skill, "enabled": t.skill not in disabled, "tools": [],
                                         "available": True, "examples": EXAMPLES.get(t.skill, {}),
                                         "description": DESCRIPTIONS.get(t.skill, "")})
            ok = t.available(self.app) if t.available else True
            s["tools"].append({"name": t.name, "description": t.description, "risk": t.risk,
                               "available": ok})
            if not ok:
                s["available"] = any(x["available"] for x in s["tools"])
        return sorted(out.values(), key=lambda s: s["name"])

    def replaced(self, ctx):
        """Server tools the asking device does itself (a phone plays music in
        its own player): {server tool name: device tool name}."""
        session = self.app.devices.get(getattr(ctx, "device_id", "") or "")
        return {r: d["name"] for d in getattr(session, "tools", None) or [] for r in d.get("replaces", [])}

    def usable(self, ctx):
        disabled = set(self.app.settings["skills"].get("disabled", []))
        replaced = self.replaced(ctx)
        out = []
        for t in self.tools.values():
            if t.skill in disabled or ctx.role not in t.roles or t.name in replaced:
                continue
            if t.available and not t.available(self.app):
                continue
            out.append(t)
        return out + list(self.device_tools(ctx).values())

    def device_tools(self, ctx):
        """The asking device's own tools (a phone's "send a message", "open
        an app"...): only for turns from that device, run on it."""
        session = self.app.devices.get(getattr(ctx, "device_id", "") or "")
        if not session or not getattr(session, "tools", None) or "device" in \
                set(self.app.settings["skills"].get("disabled", [])):
            return {}
        where = {"phone": "on the user's phone", "tv": "on the TV", "watch": "on the watch"}.get(
            session.kind, "on the user's computer" if session.kind == "desktop" else "on this device")
        out = {}
        server = self.tools
        for d in session.tools:
            t = server.get(d["name"])
            if t and (not t.available or t.available(self.app)):
                continue                    # usable server tools win
            out[d["name"]] = Tool(_device_call(session, d["name"]), d["name"],
                                  "%s (%s)" % (d["description"], where), d["parameters"], "device",
                                  risk="confirm" if d["confirm"] else "safe", summary=_summary(d))
            out[d["name"]].silent = d["silent"]
        return out

    def specs(self, ctx):
        return [t.spec() for t in self.usable(ctx)]

    def get(self, name, ctx=None):
        return self.tools.get(name) or (self.device_tools(ctx).get(name) if ctx is not None else None)

    def call(self, name, args, ctx):
        t = self.get(name, ctx)
        if not t:
            return {"error": "unknown tool %s" % name}
        if t.skill == "device":
            kwargs = dict(args or {})
        else:
            sig = inspect.signature(t.fn)
            kwargs = {k: v for k, v in (args or {}).items() if k in sig.parameters}
        t0 = time.time()
        try:
            res = t.fn(ctx, **kwargs)
        except TypeError as e:
            res = {"error": "bad arguments: %s" % e}
        except Exception as e:
            log.exception("tool %s failed", name)
            res = {"error": str(e)[:300]}
        ctx.trace_event("tool", name=name, args=args, ms=round((time.time() - t0) * 1000),
                        ok="error" not in (res or {}))
        return res if isinstance(res, dict) else {"result": res}
