"""Smart home through Home Assistant's REST API (optional: set its URL and a
long-lived access token in the settings). Entity resolution uses the asking
satellite's room: "the lights" in the kitchen means the kitchen lights."""

import difflib
import re
import time

import requests

from . import tool

SAFE_DOMAINS = ("light", "switch", "fan", "media_player", "climate", "scene", "script", "input_boolean",
                "humidifier", "vacuum", "button")
SECURE_DOMAINS = ("lock", "cover", "alarm_control_panel", "valve")
_cache = {"t": 0, "states": []}


def _cfg(app):
    return app.settings["homeassistant"]


def configured(app):
    c = _cfg(app)
    return bool(c.get("url") and c.get("token"))


def _req(app, method, path, **kw):
    c = _cfg(app)
    r = requests.request(method, c["url"].rstrip("/") + path, timeout=8,
                         headers={"Authorization": "Bearer " + c["token"]}, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def states(app, max_age=10):
    if time.time() - _cache["t"] > max_age:
        _cache["states"] = _req(app, "GET", "/api/states")
        _cache["t"] = time.time()
    return _cache["states"]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def resolve(app, query, domain=None, room=""):
    """Best matching entities for a spoken name, preferring the asking room."""
    q = _norm(query)
    room = _norm(room)
    scored = []
    for s in states(app):
        eid = s["entity_id"]
        if domain and not eid.startswith(domain + "."):
            continue
        name = _norm(s.get("attributes", {}).get("friendly_name", eid))
        score = difflib.SequenceMatcher(None, q, name).ratio()
        if q and q in name:
            score += 0.4
        generic = q in ("", "light", "lights", "the light", "the lights", "lamp", "lamps")
        if room and room in name:
            score += 0.5 if generic else 0.2
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return []
    best = scored[0][0]
    return [s for sc, s in scored if sc >= max(0.45, best - 0.15)][:10]


def _view(s):
    a = s.get("attributes", {})
    return {"entity_id": s["entity_id"], "name": a.get("friendly_name", s["entity_id"]), "state": s["state"],
            **{k: a[k] for k in ("brightness", "temperature", "current_temperature", "unit_of_measurement")
               if k in a}}


@tool("Find Home Assistant devices by name (and get their state). Generic names like 'the lights' "
      "resolve to the asking room.", {"name": ("string", "spoken name, e.g. 'kitchen lights'"),
                                      "domain": ("string", "light, switch, climate, sensor, lock, cover...")},
      available=configured)
def ha_find(ctx, name="", domain=None):
    return {"entities": [_view(s) for s in resolve(ctx.app, name, domain, ctx.room)]}


def _call(ctx, domain, service, entity_id, data):
    if not entity_id:
        return {"error": "entity_id required (use ha_find)"}
    body = dict(data or {})
    body["entity_id"] = entity_id
    res = _req(ctx.app, "POST", "/api/services/%s/%s" % (domain, service), json=body)
    _cache["t"] = 0
    return {"ok": True, "changed": [_view(s) for s in res] if isinstance(res, list) else []}


@tool("Control a Home Assistant device: lights, switches, fans, climate, media players, scenes, "
      "scripts. E.g. domain 'light', service 'turn_on', data {'brightness_pct': 40}.",
      {"domain": ("string", "service domain", list(SAFE_DOMAINS)), "service": ("string", "e.g. turn_on, turn_off, toggle, set_temperature"),
       "entity_id": ("string", "entity id, or comma separated ids"), "data": ("object", "extra service data")},
      ["domain", "service", "entity_id"], available=configured, roles=("admin", "adult", "child"))
def ha_control(ctx, domain, service, entity_id, data=None):
    if domain not in SAFE_DOMAINS:
        return {"error": "use ha_secure_control for %s" % domain}
    return _call(ctx, domain, service, entity_id, data)


@tool("Security-sensitive Home Assistant actions: locks, garage doors / covers, alarm panels, valves. "
      "Requires the user's confirmation.",
      {"domain": ("string", "service domain", list(SECURE_DOMAINS)), "service": ("string", "e.g. lock, unlock, open_cover"),
       "entity_id": ("string", "entity id"), "data": ("object", "extra service data")},
      ["domain", "service", "entity_id"], risk="confirm", available=configured, roles=("admin", "adult"),
      summary=lambda a: "%s %s" % (a.get("service", "").replace("_", " "), a.get("entity_id", "")))
def ha_secure_control(ctx, domain, service, entity_id, data=None):
    if domain not in SECURE_DOMAINS:
        return {"error": "not a secure domain"}
    return _call(ctx, domain, service, entity_id, data)


EXAMPLES = {"en": ["Turn off the lights", "Dim the living room lights to 30 percent", "Set the heating to 20 degrees", "Is the front door locked?", "Lock the front door"],
            "fr": ["Éteins la lumière", "Baisse la lumière du salon à 30 %", "Mets le chauffage à 20 degrés", "Est-ce que la porte est fermée ?", "Ferme la porte à clé"]}
