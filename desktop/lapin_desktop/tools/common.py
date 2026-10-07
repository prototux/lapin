"""Helpers shared by the device tools."""

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import unicodedata

log = logging.getLogger("tools")

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC
# In the Flatpak the commands, apps and folders the tools act on are the
# host's: commands run through `flatpak-spawn --host` (the manifest grants
# org.freedesktop.Flatpak), the host's /usr is under /run/host (host-os:ro).
IN_FLATPAK = IS_LINUX and os.path.exists("/.flatpak-info")


def dry_run():
    """LAPIN_DRY_RUN=1: resolve what would be done, without doing it (tests)."""
    return os.environ.get("LAPIN_DRY_RUN", "") not in ("", "0")


_host_which = {}


def which(*names):
    for n in names:
        p = _flatpak_which(n) if IN_FLATPAK else shutil.which(n)
        if p:
            return p
    return None


def _flatpak_which(name):
    if name not in _host_which:
        try:
            p = subprocess.run(["flatpak-spawn", "--host", "sh", "-c", 'command -v "$1"', "sh", name],
                               capture_output=True, text=True, timeout=5)
            _host_which[name] = p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
        except (OSError, subprocess.TimeoutExpired):
            _host_which[name] = None
    return _host_which[name]


def host(cmd):
    """The command to run a program of the computer (the host in the Flatpak)."""
    if IN_FLATPAK and isinstance(cmd, list):
        return ["flatpak-spawn", "--host"] + cmd
    return cmd


def _creation_flags():
    if IS_WIN:
        return subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def launch(cmd, env=None):
    """Starts a program detached from this app (it survives it). Returns a
    tool result."""
    if dry_run():
        return {"ok": True, "dry_run": cmd if isinstance(cmd, str) else " ".join(cmd)}
    try:
        subprocess.Popen(host(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=not IS_WIN, creationflags=_creation_flags(), env=env,
                         close_fds=True)
    except OSError as e:
        return {"error": "could not start %s: %s" % (cmd[0] if isinstance(cmd, list) else cmd, e)}
    return {"ok": True}


def run(cmd, timeout=8, mutate=True):
    """Runs a short command and returns (code, stdout). Commands that change
    something (mutate=True) are skipped in dry-run mode."""
    if mutate and dry_run():
        log.info("dry run: %s", " ".join(cmd))
        return 0, ""
    try:
        p = subprocess.run(host(cmd), capture_output=True, text=True, timeout=timeout, creationflags=_creation_flags())
        return p.returncode, (p.stdout or "") + (p.stderr if p.returncode else "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def open_with_system(target):
    """Opens a file, folder or URL with the default application."""
    if dry_run():
        return {"ok": True, "dry_run": "open %s" % target}
    if IS_WIN:
        try:
            os.startfile(target)        # noqa (Windows only)
            return {"ok": True}
        except OSError as e:
            return {"error": str(e)}
    if IS_MAC:
        return launch(["open", target])
    opener = which("xdg-open", "gio")
    if not opener:
        return {"error": "no xdg-open on this computer"}
    return launch([opener, "open", target] if opener.endswith("gio") else [opener, target])


def fold(text):
    """Lower case, no accents, only letters/digits separated by single spaces."""
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# ---------------------------------------------------------------- main thread
class _Invoker:
    """Runs a function in the Qt main thread and waits for its result (the
    clipboard and screen grabs must be used from there)."""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal, Slot

        class Bridge(QObject):
            call = Signal(object)

            @Slot(object)
            def run(self, job):
                fn, box, done = job
                try:
                    box["value"] = fn()
                except Exception as e:
                    box["error"] = e
                done.set()

        self.bridge = Bridge()
        self.bridge.call.connect(self.bridge.run)

    def __call__(self, fn, timeout=10):
        from PySide6.QtCore import QThread
        if QThread.currentThread() == self.bridge.thread():
            return fn()
        box, done = {}, threading.Event()
        self.bridge.call.emit((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("the UI thread did not answer")
        if "error" in box:
            raise box["error"]
        return box.get("value")


_invoker = None


def setup_main_thread():
    """Called once from the Qt main thread, after the QApplication exists."""
    global _invoker
    _invoker = _Invoker()


def in_main_thread(fn):
    if _invoker is None:
        return fn()
    return _invoker(fn)
