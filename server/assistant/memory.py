"""Context & memory: short-term dialog state per conversation, and ranking of
long-term facts for the prompt (keyword retrieval, no embedding model)."""

import math
import re
import threading
import time

STOP = set("a an the of to in on at for and or is are was were be been it this that my me i you your "
           "we our he she they them his her their what who where when how do does did with from by "
           "about as please can could would should will".split())


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in STOP and len(w) > 1]


def rank_facts(facts, query, limit=10, min_score=0.0):
    """BM25-flavoured overlap; facts: dicts with 'text'."""
    q = set(tokens(query))
    if not q:
        return facts[-limit:] if min_score <= 0 else []
    n = len(facts) or 1
    df = {}
    toks = []
    for f in facts:
        t = set(tokens(f["text"]))
        toks.append(t)
        for w in t:
            df[w] = df.get(w, 0) + 1
    scored = []
    for f, t in zip(facts, toks):
        s = sum(math.log(1 + n / df[w]) for w in q & t)
        # prefix matches: "allergy" ~ "allergic"
        s += 0.5 * sum(1 for w in q for x in t if w != x and len(w) > 3 and x.startswith(w[:4]))
        if s > 0:
            scored.append((s / (1 + 0.1 * len(t)), f))
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0] if scored else 0
    return [f for s, f in scored if s >= min_score * top][:limit]


class Conversation:
    def __init__(self, key):
        self.key = key
        self.messages = []          # OpenAI chat messages (user / assistant / tool)
        self.updated = time.time()
        self.pending = None         # action waiting for a yes / no
        self.lang = None            # language of the last request
        self.lock = threading.Lock()
        self.turns = 0

    def add(self, msg):
        self.messages.append(msg)
        self.updated = time.time()

    def history(self, max_turns):
        """The last turns, cut on a user message boundary."""
        msgs = self.messages
        starts = [i for i, m in enumerate(msgs) if m["role"] == "user"]
        if len(starts) > max_turns:
            msgs = msgs[starts[-max_turns]:]
        return list(msgs)


class Conversations:
    def __init__(self, settings):
        self.settings = settings
        self.items = {}
        self.lock = threading.Lock()

    def get(self, key):
        timeout = self.settings["assistant"].get("conversation_timeout_s", 300)
        with self.lock:
            c = self.items.get(key)
            if c is None or time.time() - c.updated > timeout:
                c = self.items[key] = Conversation(key)
            # forget long idle ones
            for k in [k for k, v in self.items.items() if time.time() - v.updated > 3600]:
                del self.items[k]
            return c

    def reset(self, key):
        with self.lock:
            self.items.pop(key, None)
