"""Remote care of embedded satellites (ESP32): their own log, sent over the
link (no serial cable needed), and firmware updates pushed through the same
WebSocket (the server's HTTP port need not be reachable from the LAN).

OTA protocol (docs/PROTOCOL.md): ota_begin {size, sha256, version}, then
binary frames 0x03 + u32 LE offset + data, then ota_end; the device answers
ota_status {state: ready | receiving | verifying | done | error, offset}.
The server waits for an ack every WINDOW bytes (flow control)."""

import collections
import hashlib
import json
import logging
import os
import threading
import time

log = logging.getLogger("firmware")

OTA_DATA = 0x03
CHUNK = 16 * 1024
WINDOW = 64 * 1024


class DeviceLogs:
    def __init__(self, app):
        self.app = app
        self.logs = {}              # device id -> {"lines": deque, "reset_reason", "boot_count", "ts"}

    def add(self, session, m):
        rec = self.logs.setdefault(session.id, {"lines": collections.deque(maxlen=600)})
        reason, boot = m.get("reset_reason"), m.get("boot_count")
        if reason and (reason, boot) != (rec.get("reset_reason"), rec.get("boot_count")):
            level = logging.WARNING if reason in ("panic", "brownout", "task_wdt", "int_wdt", "wdt") else logging.INFO
            log.log(level, "%s: booted (reset reason %s, boot %s, firmware %s)", session.name, reason, boot,
                    session.version)
        rec.update(reset_reason=reason, boot_count=boot, ts=time.time(), version=session.version)
        for line in (m.get("lines") or [])[:400]:
            line = str(line)[:300]
            rec["lines"].append(line)
            if line[:2] in ("E ", "E(") or " E (" in line[:12]:
                log.warning("%s: %s", session.name, line)
        self.app.bus.publish("device_log", device=session.id, name=session.name)

    def get(self, dev):
        rec = self.logs.get(dev)
        if not rec:
            return {"lines": [], "reset_reason": None, "boot_count": None}
        return dict(rec, lines=list(rec["lines"]))


def firmware_dir():
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "korvo", "dist"))


def available():
    """The prebuilt Korvo app image, if any: {version, size, sha256, path}."""
    d = firmware_dir()
    try:
        with open(os.path.join(d, "manifest.json")) as f:
            man = json.load(f)
    except (OSError, ValueError):
        return None
    ota = man.get("ota") or {}
    if not ota.get("file"):
        return None                 # an image without OTA support (firmware 1.0.x)
    path = os.path.join(d, os.path.basename(ota["file"]))
    if not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    if ota.get("slot_size") and size > ota["slot_size"]:
        return None
    return {"version": man.get("version"), "size": size, "sha256": ota.get("sha256"),
            "path": path, "board": man.get("board")}


class Ota:
    def __init__(self, app):
        self.app = app
        self.jobs = {}              # device id -> state dict

    def status(self, dev):
        return self.jobs.get(dev)

    def on_status(self, session, m):
        job = self.jobs.get(session.id)
        if job:
            job["device_state"] = m.get("state")
            job["acked"] = max(job.get("acked", 0), int(m.get("offset") or 0))
            if m.get("error"):
                job["error"] = str(m["error"])[:200]
            job["cond"].set()
            self.app.bus.publish("ota", device=session.id, **{k: v for k, v in job.items() if k != "cond"})

    def start(self, session):
        fw = available()
        if not fw:
            return {"error": "no firmware image in korvo/dist (run korvo/build.sh)"}
        if session.id in self.jobs and self.jobs[session.id].get("state") == "sending":
            return {"error": "an update is already running"}
        job = {"state": "sending", "version": fw["version"], "size": fw["size"], "sent": 0, "acked": 0,
               "cond": threading.Event(), "started": time.time()}
        self.jobs[session.id] = job
        threading.Thread(target=self._run, args=(session, fw, job), name="ota-%s" % session.id,
                         daemon=True).start()
        return {"ok": True, "version": fw["version"], "size": fw["size"]}

    def _wait(self, job, pred, timeout):
        end = time.time() + timeout
        while not pred():
            if job.get("device_state") == "error":
                raise RuntimeError(job.get("error") or "the device reported an error")
            left = end - time.time()
            if left <= 0:
                raise TimeoutError("no answer from the device")
            job["cond"].wait(min(left, 1.0))
            job["cond"].clear()

    def _run(self, session, fw, job):
        try:
            with open(fw["path"], "rb") as f:
                data = f.read()
            sha = hashlib.sha256(data).hexdigest()
            session.send({"type": "ota_begin", "size": len(data), "sha256": sha, "version": fw["version"]})
            self._wait(job, lambda: job.get("device_state") in ("ready", "receiving"), 20)
            for off in range(0, len(data), CHUNK):
                if not session.alive:
                    raise RuntimeError("the device disconnected")
                # flow control: at most WINDOW bytes ahead of the device's ack
                self._wait(job, lambda: off - job.get("acked", 0) < WINDOW, 30)
                session.send_binary(bytes([OTA_DATA]) + off.to_bytes(4, "little") + data[off:off + CHUNK])
                job["sent"] = off + len(data[off:off + CHUNK])
            session.send({"type": "ota_end"})
            self._wait(job, lambda: job.get("device_state") == "done", 60)
            job["state"] = "done"
            log.info("%s: firmware %s sent, the device restarts into it", session.name, fw["version"])
        except Exception as e:
            job["state"] = "error"
            job["error"] = str(e)[:200]
            log.warning("%s: firmware update failed: %s", session.name, e)
        self.app.bus.publish("ota", device=session.id, **{k: v for k, v in job.items() if k != "cond"})
