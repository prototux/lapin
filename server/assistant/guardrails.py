"""Guardrails & policy: per-user permissions, confirmation of risky actions,
untrusted tool output, rate limits."""

import collections
import json
import threading
import time

MAX_TOOL_OUTPUT = 3500


class Guardrails:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.calls = collections.defaultdict(collections.deque)

    def allow_turn(self, user):
        limit = self.app.settings["guardrails"].get("rate_per_minute", 20)
        now = time.time()
        with self.lock:
            q = self.calls[user]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def check_tool(self, tool, ctx):
        """None if the call may run now, else a result explaining why not."""
        if tool is None:
            return {"error": "unknown tool"}
        if ctx.role not in tool.roles:
            return {"error": "not allowed for this user (%s)" % ctx.role}
        if tool.risk == "confirm" and not ctx.confirmed:
            return "confirm"
        return None

    @staticmethod
    def wrap_output(result):
        """Tool output goes back to the model as data, size-limited."""
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_TOOL_OUTPUT:
            text = text[:MAX_TOOL_OUTPUT] + "...(truncated)"
        return text
