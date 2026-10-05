"""Global hotkey. X11: an exclusive grab of the key (XGrabKey), so the key
is ours alone: the Menu key then no longer opens a context menu in the
focused window. Windows and macOS: pynput (on Windows a single-key shortcut
is swallowed too). Wayland doesn't let apps grab global shortcuts: the
desktop's own shortcut settings run `lapin --activate` instead (README)."""

import logging
import os
import sys
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("hotkey")

MODIFIERS = {"ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>", "shift": "<shift>",
             "meta": "<cmd>", "super": "<cmd>", "win": "<cmd>", "cmd": "<cmd>"}
KEYS = {"space": "<space>", "return": "<enter>", "enter": "<enter>", "esc": "<esc>", "escape": "<esc>",
        "tab": "<tab>", "backspace": "<backspace>", "del": "<delete>", "delete": "<delete>",
        "ins": "<insert>", "insert": "<insert>", "home": "<home>", "end": "<end>", "pgup": "<page_up>",
        "pgdown": "<page_down>", "up": "<up>", "down": "<down>", "left": "<left>", "right": "<right>",
        "pause": "<pause>", "print": "<print_screen>", "menu": "<menu>"}


def is_wayland():
    return sys.platform.startswith("linux") and (os.environ.get("XDG_SESSION_TYPE") == "wayland"
                                                 or bool(os.environ.get("WAYLAND_DISPLAY")))


def qt_to_pynput(seq, mac=None):
    """"Ctrl+Alt+Space" (Qt portable text) -> "<ctrl>+<alt>+<space>"."""
    mac = sys.platform == "darwin" if mac is None else mac
    parts = [p for p in seq.replace(" ", "").split("+") if p]
    if seq.strip().endswith("++"):
        parts.append("+")
    if not parts:
        raise ValueError("empty shortcut")
    out = []
    for p in parts[:-1]:
        k = p.lower()
        if mac and k in ("ctrl", "control"):
            k = "cmd"           # Qt calls the Command key "Ctrl" on macOS
        elif mac and k == "meta":
            k = "ctrl"
        if k not in MODIFIERS:
            raise ValueError("unknown modifier %r" % p)
        out.append(MODIFIERS[k])
    key = parts[-1]
    k = key.lower()
    if k in KEYS:
        out.append(KEYS[k])
    elif len(k) > 1 and k[0] == "f" and k[1:].isdigit():
        out.append("<%s>" % k)
    elif len(key) == 1:
        out.append(k)
    else:
        raise ValueError("unsupported key %r" % key)
    return "+".join(out)


# Qt key names -> X keysym names (letters, digits and F-keys are the same)
X_KEYS = {"space": "space", "return": "Return", "enter": "Return", "esc": "Escape", "escape": "Escape",
          "tab": "Tab", "backspace": "BackSpace", "del": "Delete", "delete": "Delete", "ins": "Insert",
          "insert": "Insert", "home": "Home", "end": "End", "pgup": "Prior", "pgdown": "Next", "up": "Up",
          "down": "Down", "left": "Left", "right": "Right", "pause": "Pause", "print": "Print",
          "menu": "Menu"}


def qt_to_x11(seq):
    """"Ctrl+Alt+Space" -> (modifier mask, keysym name)."""
    from Xlib import X
    masks = {"ctrl": X.ControlMask, "control": X.ControlMask, "alt": X.Mod1Mask, "shift": X.ShiftMask,
             "meta": X.Mod4Mask, "super": X.Mod4Mask, "win": X.Mod4Mask}
    parts = [p for p in seq.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError("empty shortcut")
    mods = 0
    for p in parts[:-1]:
        if p.lower() not in masks:
            raise ValueError("unknown modifier %r" % p)
        mods |= masks[p.lower()]
    key = parts[-1]
    k = key.lower()
    if k in X_KEYS:
        name = X_KEYS[k]
    elif len(k) > 1 and k[0] == "f" and k[1:].isdigit():
        name = key.upper()
    elif len(key) == 1 and key.isalnum():
        name = k
    else:
        raise ValueError("unsupported key %r" % key)
    return mods, name


class X11Grab:
    """XGrabKey on the root window, in its own thread and display connection."""

    def __init__(self, seq, fire):
        from Xlib import X, XK, display, error
        self.X = X
        self.fire = fire
        self.mods, name = qt_to_x11(seq)
        self.d = display.Display()
        self.root = self.d.screen().root
        self.code = self.d.keysym_to_keycode(XK.string_to_keysym(name))
        if not self.code:
            raise ValueError("no key %r on this keyboard" % name)
        failed = []
        self.d.set_error_handler(lambda err, *a: failed.append(err))
        # also with NumLock (Mod2) and CapsLock (Lock) on
        self.variants = [self.mods | extra for extra in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask)]
        for m in self.variants:
            self.root.grab_key(self.code, m, True, X.GrabModeAsync, X.GrabModeAsync)
        self.d.sync()
        if failed:
            self.d.close()
            raise RuntimeError("the shortcut is already taken by another program")
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="x11-hotkey", daemon=True)
        self.thread.start()

    def _loop(self):
        X = self.X
        while self.running:
            try:
                ev = self.d.next_event()
            except Exception:
                break
            if ev.type == X.KeyPress and ev.detail == self.code:
                self.fire()

    def stop(self):
        self.running = False
        try:
            for m in self.variants:
                self.root.ungrab_key(self.code, m)
            self.d.flush()
            self.d.close()          # also wakes next_event() up
        except Exception:
            pass


def _swallow(combo):
    """Windows: a single-key shortcut (the Menu key) must not also reach the
    focused app; the listener still sees it."""
    from pynput import keyboard
    key = keyboard.HotKey.parse(combo)[0]
    vk = getattr(key, "vk", None) or getattr(getattr(key, "value", None), "vk", None)
    holder = {}

    def filt(msg, data):
        if vk is not None and data.vkCode == vk:
            holder["l"].suppress_event()
        return True
    filt.holder = holder
    return filt


def is_x11():
    return sys.platform.startswith("linux") and not is_wayland() and bool(os.environ.get("DISPLAY"))


class GlobalHotkey(QObject):
    triggered = Signal()

    def __init__(self):
        super().__init__()
        self.listener = None
        self.error = ""

    def set(self, seq):
        """Registers the shortcut; False (and self.error) when it can't."""
        self.stop()
        self.error = ""
        if not seq:
            return False
        if is_wayland():
            self.error = "wayland"
            return False
        if is_x11():
            try:
                self.listener = X11Grab(seq, self._fire)
                log.info("global hotkey: %s (exclusive X11 grab)", seq)
                return True
            except Exception as e:
                self.error = str(e) or e.__class__.__name__
                log.warning("global hotkey %s unavailable: %s", seq, self.error)
                self.listener = None
                return False
        try:
            combo = qt_to_pynput(seq)
            from pynput import keyboard
            kw = {}
            if sys.platform.startswith("win") and "+" not in combo:
                kw["win32_event_filter"] = _swallow(combo)
            self.listener = keyboard.GlobalHotKeys({combo: self._fire}, **kw)
            if kw:
                kw["win32_event_filter"].holder["l"] = self.listener
            self.listener.daemon = True
            self.listener.start()
        except Exception as e:      # no X display, no accessibility permission, bad key...
            self.error = str(e) or e.__class__.__name__
            log.warning("global hotkey %s unavailable: %s", seq, self.error)
            self.listener = None
            return False
        log.info("global hotkey: %s (%s)", seq, combo)
        return True

    def _fire(self):
        self.triggered.emit()       # from pynput's thread: queued to the UI thread

    def stop(self):
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
