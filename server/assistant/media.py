"""Media service: internet radio and music libraries (Jellyfin, Subsonic /
Navidrome) decoded by ffmpeg and streamed to one or more satellites. A group
starts in sync and shares one decoder; a queue of tracks plays as one
continuous stream (next / previous / pause / resume). The satellites duck
it under speech by themselves."""

import difflib
import logging
import subprocess
import threading
import time

log = logging.getLogger("media")
CHUNK = 48000 * 2 * 2 // 20        # 50 ms of 48 kHz stereo s16


class Player:
    """Plays a list of items: {"title", "artist"?, "album"?, "url", "live"?}."""

    def __init__(self, app, items, sessions, name, source="radio"):
        self.app = app
        self.items = list(items)
        self.index = 0
        self.name = name
        self.source = source
        self.sessions = list(sessions)
        self.stop_ev = threading.Event()
        self.running = threading.Event()      # cleared while paused
        self.running.set()
        self.jump = None                      # pending index change
        self.started = time.time()
        self.error = None
        self.proc = None
        self.streams = {}                     # device id -> OutStream
        self.lock = threading.Lock()
        threading.Thread(target=self._run, name="media", daemon=True).start()

    @property
    def station(self):          # compatibility with radio-only callers
        return {"name": self.name}

    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def _run(self):
        gain = self.app.settings["media"].get("default_volume_db", -6)
        # internet streams start in bursts: begin with a comfortable buffer
        start = time.time() + 1.6
        for s in self.sessions:
            self.streams[s.id] = s.open_stream("media", 48000, 2, start_at=start, gain_db=gain,
                                               prebuffer_ms=1500, lead=3.0)
        try:
            while not self.stop_ev.is_set() and 0 <= self.index < len(self.items):
                item = self.items[self.index]
                ok = self._play_item(item)
                if self.stop_ev.is_set():
                    break
                with self.lock:
                    if self.jump is not None:
                        self.index, self.jump = self.jump, None
                    else:
                        self.index += 1
                if not ok and item.get("live"):
                    break
                self.app.bus.publish("media", event="track", station=self.name, track=self.track_view())
        finally:
            for st in list(self.streams.values()):
                st.close(drain=not self.stop_ev.is_set())
            self.app.media._ended(self)

    def _play_item(self, item):
        cmd = ["ffmpeg", "-loglevel", "error", "-nostdin", "-reconnect", "1", "-reconnect_streamed", "1",
               "-reconnect_delay_max", "5"]
        if item.get("headers"):
            cmd += ["-headers", item["headers"]]
        cmd += ["-i", item["url"], "-vn", "-f", "s16le", "-ac", "2", "-ar", "48000", "pipe:1"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            self.error = str(e)
            return False
        got = 0
        try:
            while not self.stop_ev.is_set():
                self.running.wait()
                if self.jump is not None or self.stop_ev.is_set():
                    break
                data = self.proc.stdout.read(CHUNK)
                if not data:
                    if not got:
                        self.error = self.proc.stderr.read().decode(errors="replace").strip()[:300] or "no audio"
                        log.warning("media: %s: %s", item.get("title"), self.error)
                    break
                got += len(data)
                for sid, st in list(self.streams.items()):
                    if not st.session.alive or st.closed:
                        self.streams.pop(sid, None)
                        continue
                    st.write(data, self.stop_ev)
                if not self.streams:
                    self.stop_ev.set()
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
        return got > 0

    # ------------------------------------------------------------ control
    def stop(self):
        self.stop_ev.set()
        self.running.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def skip(self, delta):
        with self.lock:
            target = max(0, self.index + delta)
            if target >= len(self.items):
                return False
            self.jump = target
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.running.set()
        return True

    def pause(self):
        if not self.running.is_set():
            return
        self.running.clear()
        for st in list(self.streams.values()):
            st.pause()

    def resume(self):
        if self.running.is_set():
            return
        for st in list(self.streams.values()):
            st.resume()
        self.running.set()

    def drop(self, session):
        """Stops this player on one device only: closes its music stream (and
        nothing else: a reply being spoken keeps playing)."""
        st = self.streams.pop(session.id, None)
        if st:
            st.close(drain=False)

    def track_view(self):
        it = self.current() or {}
        return {"title": it.get("title", ""), "artist": it.get("artist", ""), "album": it.get("album", ""),
                "position": self.index + 1, "count": len(self.items)}

    def view(self):
        return {"station": self.name, "source": self.source, "since": self.started,
                "paused": not self.running.is_set(), "track": self.track_view()}


class MediaService:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.by_device = {}         # device id -> Player

    def find_station(self, name):
        if name.startswith(("http://", "https://")):
            return {"name": name, "url": name}
        stations = self.app.settings["media"].get("stations", [])
        names = {s["name"].lower(): s for s in stations}
        n = name.lower().replace(" radio", "").strip()
        for k, s in names.items():
            if n == k or n in k:
                return s
        hit = difflib.get_close_matches(n, list(names), n=1, cutoff=0.55)
        return names[hit[0]] if hit else None

    def play(self, station, sessions):
        """An internet radio station."""
        return self.play_items([{"title": station["name"], "url": station["url"], "live": True}], sessions,
                               station["name"], "radio")

    def play_items(self, items, sessions, name, source):
        if not items or not sessions:
            return None
        for s in sessions:
            self.stop(s)
        p = Player(self.app, items, sessions, name, source)
        with self.lock:
            for s in sessions:
                self.by_device[s.id] = p
        self.app.bus.publish("media", event="play", station=name, devices=[s.name for s in sessions])
        return p

    def player(self, session):
        with self.lock:
            return self.by_device.get(session.id)

    def stop(self, session):
        with self.lock:
            p = self.by_device.pop(session.id, None)
            if p:
                p.sessions = [s for s in p.sessions if s.id != session.id]
                p.drop(session)
                if not any(v is p for v in self.by_device.values()):
                    p.stop()
        if p:
            self.app.bus.publish("media", event="stop", device=session.name)
        return bool(p)

    def control(self, session, action):
        """pause / resume / next / previous / stop; False if nothing to do."""
        if action == "stop":
            return self.stop(session)
        p = self.player(session)
        if not p:
            return False
        if action == "pause":
            p.pause()
        elif action == "resume":
            p.resume()
        elif action == "next":
            return p.skip(1)
        elif action == "previous":
            return p.skip(-1)
        else:
            return False
        return True

    def _ended(self, player):
        with self.lock:
            for k in [k for k, v in self.by_device.items() if v is player]:
                del self.by_device[k]
        if player.error and not player.stop_ev.is_set():
            log.warning("media %s stopped: %s", player.name, player.error)

    def here(self, ctx):
        """The player of the asking device (or of the only satellite)."""
        for s in self.app.devices.resolve("here", ctx):
            p = self.player(s)
            if p:
                return p
        with self.lock:
            players = list({id(p): p for p in self.by_device.values()}.values())
        return players[0] if len(players) == 1 else None

    def describe_for(self, ctx):
        p = self.here(ctx)
        if not p:
            return ""
        if p.source == "radio":
            return "the radio station %s%s" % (p.name, " (paused)" if not p.running.is_set() else "")
        t = p.track_view()
        return "'%s' by %s%s (%s, track %d of %d%s)" % (
            t["title"], t["artist"] or "?", ", album '%s'" % t["album"] if t["album"] else "", p.source,
            t["position"], t["count"], ", paused" if not p.running.is_set() else "")

    def status(self):
        with self.lock:
            out = {}
            for dev, p in self.by_device.items():
                s = self.app.devices.get(dev)
                out[s.name if s else dev] = p.view()
            return out
