"""SQLite storage: devices, timers / alarms / reminders, memory facts,
users and channel identities, conversation turns with their traces."""

import hashlib
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, token_hash TEXT, name TEXT, room TEXT DEFAULT '', kind TEXT,
    status TEXT DEFAULT 'pending', created REAL, last_seen REAL, info TEXT DEFAULT '{}',
    config TEXT DEFAULT '{}', user TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, label TEXT DEFAULT '', due REAL,
    created REAL, duration REAL DEFAULT 0, device_id TEXT DEFAULT '', user TEXT DEFAULT '',
    channel TEXT DEFAULT 'voice', chat_id TEXT DEFAULT '', status TEXT DEFAULT 'active',
    repeat TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT DEFAULT '', text TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS users (
    name TEXT PRIMARY KEY, role TEXT DEFAULT 'adult', created REAL
);
CREATE TABLE IF NOT EXISTS identities (
    channel TEXT, external_id TEXT, user TEXT, PRIMARY KEY (channel, external_id)
);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, ts REAL, device_id TEXT, channel TEXT, user TEXT,
    transcript TEXT, reply TEXT, route TEXT, status TEXT, trace TEXT, audio TEXT
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            cols = [r[1] for r in self.db.execute("PRAGMA table_info(users)").fetchall()]
            if "settings" not in cols:
                self.db.execute("ALTER TABLE users ADD COLUMN settings TEXT DEFAULT '{}'")
            if not self.db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                self.db.execute("INSERT INTO users (name, role, created) VALUES ('household', 'admin', ?)",
                                (time.time(),))

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            return cur.lastrowid

    # ------------------------------------------------------------ devices
    def device(self, device_id):
        d = self.one("SELECT * FROM devices WHERE id = ?", (device_id,))
        if d:
            d["info"] = json.loads(d["info"] or "{}")
            d["config"] = json.loads(d["config"] or "{}")
        return d

    def devices(self):
        out = self.q("SELECT * FROM devices ORDER BY created")
        for d in out:
            d["info"] = json.loads(d["info"] or "{}")
            d["config"] = json.loads(d["config"] or "{}")
            d.pop("token_hash", None)
        return out

    def register_device(self, device_id, token, name, room, kind, info, status):
        self.x("INSERT INTO devices (id, token_hash, name, room, kind, status, created, last_seen, info) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (device_id, token_hash(token), name, room, kind, status, time.time(), time.time(),
                json.dumps(info)))

    def update_device(self, device_id, **fields):
        for k in ("info", "config"):
            if k in fields:
                fields[k] = json.dumps(fields[k])
        if "token" in fields:
            fields["token_hash"] = token_hash(fields.pop("token"))
        sets = ", ".join("%s = ?" % k for k in fields)
        self.x("UPDATE devices SET %s WHERE id = ?" % sets, (*fields.values(), device_id))

    def delete_device(self, device_id):
        self.x("DELETE FROM devices WHERE id = ?", (device_id,))

    # ------------------------------------------------------------ timers
    def add_timer(self, kind, due, label="", duration=0, device_id="", user="", channel="voice",
                  chat_id="", repeat=""):
        return self.x("INSERT INTO timers (kind, label, due, created, duration, device_id, user, channel, "
                      "chat_id, repeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (kind, label, due, time.time(), duration, device_id, user, channel, chat_id, repeat))

    def active_timers(self, kind=None):
        if kind:
            return self.q("SELECT * FROM timers WHERE status = 'active' AND kind = ? ORDER BY due", (kind,))
        return self.q("SELECT * FROM timers WHERE status = 'active' ORDER BY due")

    def set_timer_status(self, timer_id, status):
        self.x("UPDATE timers SET status = ? WHERE id = ?", (status, timer_id))

    # ------------------------------------------------------------ memory
    def facts(self, user=None):
        if user:
            return self.q("SELECT * FROM facts WHERE user IN (?, '') ORDER BY created", (user,))
        return self.q("SELECT * FROM facts ORDER BY created")

    def add_fact(self, text, user=""):
        return self.x("INSERT INTO facts (user, text, created) VALUES (?, ?, ?)", (user, text, time.time()))

    def delete_fact(self, fact_id):
        self.x("DELETE FROM facts WHERE id = ?", (fact_id,))

    # ------------------------------------------------------------ users
    def users(self):
        return self.q("SELECT * FROM users ORDER BY name")

    def user_settings(self, name):
        r = self.one("SELECT settings FROM users WHERE name = ?", (name,))
        return json.loads((r or {}).get("settings") or "{}")

    def set_user_settings(self, name, settings):
        self.x("UPDATE users SET settings = ? WHERE name = ?", (json.dumps(settings), name))

    def user_role(self, name):
        u = self.one("SELECT role FROM users WHERE name = ?", (name,))
        return u["role"] if u else "guest"

    def identity(self, channel, external_id):
        r = self.one("SELECT user FROM identities WHERE channel = ? AND external_id = ?", (channel, str(external_id)))
        return r["user"] if r else None

    # ------------------------------------------------------------ turns
    def save_turn(self, t):
        self.x("INSERT OR REPLACE INTO turns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (t["id"], t["ts"], t.get("device_id", ""), t.get("channel", ""), t.get("user", ""),
                t.get("transcript", ""), t.get("reply", ""), t.get("route", ""), t.get("status", ""),
                json.dumps(t.get("trace", {})), t.get("audio", "")))

    def turns(self, limit=100, before=None):
        if before:
            rows = self.q("SELECT * FROM turns WHERE ts < ? ORDER BY ts DESC LIMIT ?", (before, limit))
        else:
            rows = self.q("SELECT * FROM turns ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["trace"] = json.loads(r["trace"] or "{}")
        return rows

    def purge_turns(self, older_than):
        old = self.q("SELECT audio FROM turns WHERE ts < ? AND audio != ''", (older_than,))
        self.x("DELETE FROM turns WHERE ts < ?", (older_than,))
        return [r["audio"] for r in old]

    # ------------------------------------------------------------ kv
    def kv_get(self, key, default=None):
        r = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(r["value"]) if r else default

    def kv_set(self, key, value):
        self.x("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
