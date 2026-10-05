"""Optional plugins.

A plugin is a sub-package of satagent.plugins exposing a `Plugin` class
(subclass of PluginBase). The agent never imports a plugin directly: it
discovers them here, so removing a plugin's folder (or setting
"enabled": false under config["plugins"][name]) removes it cleanly.

Events delivered to on_event(name, data):
    boot                                    agent started
    state      {state, muted, reason}       idle / listening / thinking / speaking
    wake       {doa, source, score, ...}    a wake word or button started a turn
    meters     {track, vad, out_db, ...}    ~12 Hz engine meters
    link       {up, status}                 server connection (status: online/pending/offline)
    volume     {volume}                     volume changed
    mute       {muted}                      microphone mute toggled
    error      {reason}
    alarm      {on}                         an alarm / timer is ringing
    led        {pattern, color, ms}         explicit request from the server
    shutdown
A plugin may serve a web panel: static/panel.js (+ optional panel.css),
loaded in the satellite web UI; its API is reachable at
/api/plugins/<name>/<action> -> Plugin.api(action, data).
"""

import importlib
import logging
import os
import pkgutil

log = logging.getLogger("plugins")


class PluginBase:
    name = "base"
    title = "Plugin"
    description = ""

    def __init__(self, agent, settings):
        self.agent = agent
        self.settings = settings

    def start(self):
        pass

    def stop(self):
        pass

    def on_event(self, event, data):
        pass

    def get_settings(self):
        return dict(self.settings)

    def set_settings(self, changes):
        """Applies and returns the new settings; the agent persists them."""
        self.settings.update(changes)
        return self.get_settings()

    def api(self, action, data):
        return {"error": "unknown action %s" % action}

    def status(self):
        return {}

    @property
    def static_dir(self):
        d = os.path.join(os.path.dirname(importlib.import_module(self.__module__).__file__), "static")
        return d if os.path.isdir(d) else None


def discover():
    here = os.path.dirname(__file__)
    return sorted(m.name for m in pkgutil.iter_modules([here]) if m.ispkg)


def load(agent):
    plugins = {}
    for name in discover():
        settings = agent.cfg.plugin(name)
        if settings.get("enabled") is False:
            log.info("plugin %s disabled", name)
            continue
        try:
            mod = importlib.import_module("%s.%s" % (__name__, name))
            plugin = mod.Plugin(agent, settings)
            plugin.start()
            plugins[name] = plugin
            log.info("plugin %s loaded", name)
        except Exception:
            log.exception("plugin %s failed to load, skipped", name)
    return plugins
