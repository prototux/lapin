"""In-process event bus: device states, turns, traces, logs. The web UI
subscribes through server-sent events."""

import collections
import logging
import queue
import threading
import time


class Bus:
    def __init__(self):
        self.lock = threading.Lock()
        self.queues = set()
        self.handlers = collections.defaultdict(list)
        self.recent = collections.deque(maxlen=300)

    def publish(self, topic, **data):
        msg = {"topic": topic, "ts": time.time(), **data}
        with self.lock:
            self.recent.append(msg)
            qs = list(self.queues)
            hs = list(self.handlers.get(topic, ())) + list(self.handlers.get("*", ()))
        for q in qs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass
        for h in hs:
            try:
                h(msg)
            except Exception:
                logging.getLogger("bus").exception("handler failed for %s", topic)

    def subscribe(self, maxsize=500):
        q = queue.Queue(maxsize=maxsize)
        with self.lock:
            self.queues.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.queues.discard(q)

    def on(self, topic, fn):
        with self.lock:
            self.handlers[topic].append(fn)


class BusLogHandler(logging.Handler):
    """Mirrors log records to the bus (and keeps the last ones for the UI)."""

    def __init__(self, bus):
        super().__init__(logging.INFO)
        self.bus = bus
        self.lines = collections.deque(maxlen=1000)

    def emit(self, record):
        try:
            line = {"ts": record.created, "level": record.levelname, "name": record.name,
                    "msg": record.getMessage()}
            self.lines.append(line)
            self.bus.publish("log", **line)
        except Exception:
            pass
