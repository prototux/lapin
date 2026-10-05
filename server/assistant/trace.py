"""Per-turn traces: when each stage started / ended, for the latency
waterfall in the web UI."""

import time
import uuid


class Trace:
    def __init__(self, kind="voice", t0=None):
        self.id = uuid.uuid4().hex[:12]
        self.t0 = t0 or time.time()
        self.kind = kind
        self.marks = {}         # name -> ms since t0
        self.spans = []         # (name, start_ms, end_ms, info)
        self.events = []        # (ms, kind, data)
        self._open = {}

    def ms(self, t=None):
        return round(((t or time.time()) - self.t0) * 1000, 1)

    def mark(self, name, t=None):
        if name not in self.marks:
            self.marks[name] = self.ms(t)

    def start(self, name):
        self._open[name] = self.ms()

    def end(self, name, **info):
        s = self._open.pop(name, None)
        if s is not None:
            self.spans.append((name, s, self.ms(), info))

    def event(self, kind, **data):
        self.events.append((self.ms(), kind, data))

    def to_dict(self):
        return {"id": self.id, "t0": self.t0, "kind": self.kind, "marks": self.marks,
                "spans": [{"name": n, "start": s, "end": e, "info": i} for n, s, e, i in self.spans],
                "events": [{"ms": m, "kind": k, "data": d} for m, k, d in self.events]}
