"""Computer actions: media keys/MPRIS, system volume, lock, power, system info."""

import ctypes
import os
import platform
import re
import shutil
import threading
import time

from .common import IS_LINUX, IS_MAC, IS_WIN, dry_run, in_main_thread, launch, run, which

# ---------------------------------------------------------------- Windows keys
VK = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1,
      "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def _press(vk, times=1):
    if dry_run():
        return
    user32 = ctypes.windll.user32       # noqa (Windows only)
    for _ in range(times):
        user32.keybd_event(vk, 0, 1, 0)          # KEYEVENTF_EXTENDEDKEY
        user32.keybd_event(vk, 0, 1 | 2, 0)      # + KEYEVENTF_KEYUP
        time.sleep(0.01)


# ---------------------------------------------------------------- media
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_METHODS = {"play": "Play", "pause": "Pause", "next": "Next", "previous": "Previous"}


def _mpris_players():
    """[(bus name, identity-ish name, playback status)] of the MPRIS players."""
    from PySide6.QtDBus import QDBusConnection, QDBusInterface
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise RuntimeError("no D-Bus session bus")
    names = bus.interface().registeredServiceNames().value() or []
    out = []
    for n in names:
        if not n.startswith(MPRIS_PREFIX):
            continue
        props = QDBusInterface(n, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", bus)
        status = ""
        reply = props.call("Get", "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
        args = reply.arguments()
        if args:
            v = args[0]
            status = str(getattr(v, "variant", lambda: v)() if hasattr(v, "variant") else v)
        out.append((n, n[len(MPRIS_PREFIX):].split(".")[0], status))
    return out


def _mpris(action):
    def do():
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
        players = _mpris_players()
        if not players:
            return {"error": "no media player is running"}
        rank = {"Playing": 0, "Paused": 1}
        if action == "play":
            players.sort(key=lambda p: {"Paused": 0, "Playing": 1}.get(p[2], 2))
            targets = players[:1]
        elif action == "pause":
            targets = [p for p in players if p[2] == "Playing"] or players[:1]
        else:
            players.sort(key=lambda p: rank.get(p[2], 2))
            targets = players[:1]
        method = MPRIS_METHODS[action]
        if dry_run():
            return {"ok": True, "dry_run": "%s on %s" % (method, ", ".join(t[1] for t in targets))}
        bus = QDBusConnection.sessionBus()
        for name, _, _ in targets:
            QDBusInterface(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", bus).call(method)
        return {"ok": True, "player": ", ".join(t[1] for t in targets)}
    return in_main_thread(do)


def computer_media(action=""):
    action = str(action).lower().strip()
    if action not in MPRIS_METHODS:
        return {"error": "action must be play, pause, next or previous"}
    if IS_WIN:
        _press(VK[action])
        return {"ok": True, "dry_run": "media key %s" % action} if dry_run() else {"ok": True}
    if IS_MAC:
        verb = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}[action]
        script = ('repeat with p in {"Spotify", "Music"}\n'
                  '  if application p is running then\n'
                  '    tell application p to %s\n'
                  '    return p\n'
                  '  end if\n'
                  'end repeat\n'
                  'return ""') % verb
        if dry_run():
            return {"ok": True, "dry_run": "osascript: %s" % verb}
        code, out = run(["osascript", "-e", script])
        if code != 0:
            return {"error": out.strip()[:200] or "osascript failed"}
        if not out.strip():
            return {"error": "neither Music nor Spotify is running"}
        return {"ok": True, "player": out.strip()}
    if which("playerctl"):
        if dry_run():
            return {"ok": True, "dry_run": "playerctl %s" % action}
        code, out = run(["playerctl", action])
        return {"ok": True} if code == 0 else {"error": out.strip()[:200] or "no media player is running"}
    try:
        return _mpris(action)
    except Exception as e:
        return {"error": "media control failed: %s" % e}


# ---------------------------------------------------------------- volume
def _get_volume():
    """(level 0-100, muted) or (None, None)."""
    if IS_LINUX:
        if which("wpctl"):
            code, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], mutate=False)
            m = re.search(r"([\d.]+)", out or "")
            if code == 0 and m:
                return round(float(m.group(1)) * 100), "MUTED" in out
        if which("pactl"):
            code, out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], mutate=False)
            m = re.search(r"(\d+)%", out or "")
            _, mute = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], mutate=False)
            if code == 0 and m:
                return int(m.group(1)), "yes" in (mute or "")
    if IS_MAC:
        code, out = run(["osascript", "-e", "get {output volume, output muted} of (get volume settings)"],
                        mutate=False)
        parts = [p.strip() for p in (out or "").split(",")]
        if code == 0 and parts and parts[0].isdigit():
            return int(parts[0]), len(parts) > 1 and parts[1] == "true"
    return None, None


def computer_volume(level=None, change=None, mute=None):
    try:
        level = None if level in (None, "") else max(0, min(100, int(float(level))))
        change = None if change in (None, "") else int(float(change))
    except (TypeError, ValueError):
        return {"error": "level and change must be numbers"}
    if level is None and change is None and mute is None:
        return {"error": "give a level (0-100), a change (+/-), or mute"}
    current, muted = _get_volume()
    if level is None and change is not None:
        level = max(0, min(100, (current if current is not None else 50) + change))
    res = {"ok": True}
    if IS_LINUX:
        if which("wpctl"):
            sink = "@DEFAULT_AUDIO_SINK@"
            if level is not None:
                run(["wpctl", "set-volume", sink, "%.2f" % (level / 100)])
                if mute is None:
                    run(["wpctl", "set-mute", sink, "0"])
            if mute is not None:
                run(["wpctl", "set-mute", sink, "1" if mute else "0"])
        elif which("pactl"):
            if level is not None:
                run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % level])
                if mute is None:
                    run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
            if mute is not None:
                run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
        else:
            return {"error": "neither wpctl nor pactl is installed"}
    elif IS_MAC:
        if level is not None:
            run(["osascript", "-e", "set volume output volume %d" % level])
        if mute is not None:
            run(["osascript", "-e", "set volume %s" % ("with output muted" if mute else "without output muted")])
    elif IS_WIN:
        # no volume API without extra modules: the volume keys move by 2%
        if mute is not None:
            _press(VK["mute"])
        if level is not None:
            if change is not None and current is None:
                _press(VK["volume_up" if change > 0 else "volume_down"], max(1, abs(change) // 2))
            else:
                _press(VK["volume_down"], 50)
                _press(VK["volume_up"], level // 2)
    if level is not None:
        res["level"] = level
    if mute is not None:
        res["muted"] = bool(mute)
    if dry_run():
        res["dry_run"] = "volume %s (was %s)" % (level, current)
    return res


# ---------------------------------------------------------------- lock / power
def lock_screen():
    if IS_WIN:
        if dry_run():
            return {"ok": True, "dry_run": "LockWorkStation"}
        ok = ctypes.windll.user32.LockWorkStation()     # noqa (Windows only)
        return {"ok": True} if ok else {"error": "could not lock the session"}
    if IS_MAC:
        cg = "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession"
        if os.path.exists(cg):
            return launch([cg, "-suspend"])
        return launch(["pmset", "displaysleepnow"])
    cmds = []
    if which("loginctl"):
        cmds.append(["loginctl", "lock-session"] + ([os.environ["XDG_SESSION_ID"]]
                                                      if os.environ.get("XDG_SESSION_ID") else []))
    if which("xdg-screensaver"):
        cmds.append(["xdg-screensaver", "lock"])
    if which("dbus-send"):
        cmds.append(["dbus-send", "--session", "--dest=org.freedesktop.ScreenSaver", "--type=method_call",
                     "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"])
    if not cmds:
        return {"error": "no way to lock the screen found (loginctl, xdg-screensaver)"}
    if dry_run():
        return {"ok": True, "dry_run": " ".join(cmds[0])}
    for c in cmds:
        code, out = run(c)
        if code == 0:
            return {"ok": True}
    return {"error": "locking failed: %s" % out.strip()[:200]}


def _power_command(action):
    if IS_WIN:
        return {"shutdown": ["shutdown", "/s", "/t", "0"], "reboot": ["shutdown", "/r", "/t", "0"],
                "suspend": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                "logout": ["shutdown", "/l"]}[action]
    if IS_MAC:
        verb = {"shutdown": "shut down", "reboot": "restart", "suspend": "sleep", "logout": "log out"}[action]
        return ["osascript", "-e", 'tell application "System Events" to %s' % verb]
    if action == "logout":
        sid = os.environ.get("XDG_SESSION_ID")
        if sid and which("loginctl"):
            return ["loginctl", "terminate-session", sid]
        if which("gnome-session-quit"):
            return ["gnome-session-quit", "--logout", "--no-prompt"]
        if which("qdbus"):
            return ["qdbus", "org.kde.ksmserver", "/KSMServer", "logout", "0", "0", "0"]
        if which("xfce4-session-logout"):
            return ["xfce4-session-logout", "--logout"]
        return ["loginctl", "terminate-user", os.environ.get("USER", "")]
    return ["systemctl", {"shutdown": "poweroff", "reboot": "reboot", "suspend": "suspend"}[action]]


def power(action=""):
    action = str(action).lower().strip()
    if action not in ("shutdown", "reboot", "suspend", "logout"):
        return {"error": "action must be shutdown, reboot, suspend or logout"}
    cmd = _power_command(action)
    if dry_run():
        return {"ok": True, "dry_run": " ".join(cmd)}
    # a few seconds later, so the answer can be sent and spoken first
    threading.Timer(4.0, lambda: launch(cmd)).start()
    return {"ok": True, "action": action, "in_seconds": 4}


# ---------------------------------------------------------------- info
def _battery():
    if IS_LINUX:
        for d in sorted(os.listdir("/sys/class/power_supply")) if os.path.isdir("/sys/class/power_supply") else []:
            base = os.path.join("/sys/class/power_supply", d)
            try:
                with open(os.path.join(base, "type")) as f:
                    if f.read().strip() != "Battery":
                        continue
                with open(os.path.join(base, "capacity")) as f:
                    cap = int(f.read().strip())
                with open(os.path.join(base, "status")) as f:
                    status = f.read().strip().lower()
                return {"percent": cap, "charging": status == "charging", "status": status}
            except (OSError, ValueError):
                continue
        return None
    if IS_WIN:
        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                        ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        s = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)) and s.BatteryFlag != -128 \
                and (s.BatteryFlag & 128) == 0 and 0 <= s.BatteryLifePercent <= 100:
            return {"percent": s.BatteryLifePercent, "charging": s.ACLineStatus == 1}
        return None
    if IS_MAC:
        code, out = run(["pmset", "-g", "batt"], mutate=False)
        m = re.search(r"(\d+)%;\s*([\w ]+);", out or "")
        if code == 0 and m:
            return {"percent": int(m.group(1)), "charging": m.group(2).strip() in ("charging", "charged"),
                    "status": m.group(2).strip()}
    return None


def _uptime_s():
    try:
        if IS_LINUX:
            with open("/proc/uptime") as f:
                return float(f.read().split()[0])
        if IS_WIN:
            k = ctypes.windll.kernel32
            k.GetTickCount64.restype = ctypes.c_ulonglong
            return k.GetTickCount64() / 1000
        if IS_MAC:
            code, out = run(["sysctl", "-n", "kern.boottime"], mutate=False)
            m = re.search(r"sec = (\d+)", out or "")
            if m:
                return time.time() - int(m.group(1))
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _human_duration(s):
    s = int(s)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    parts = (["%d days" % d] if d else []) + (["%d hours" % h] if h else []) + ["%d minutes" % m]
    return " ".join(parts[:2])


def _os_name():
    if IS_LINUX:
        try:
            info = platform.freedesktop_os_release()
            return "%s (Linux %s)" % (info.get("PRETTY_NAME") or info.get("NAME"), platform.release())
        except OSError:
            return "Linux %s" % platform.release()
    if IS_MAC:
        return "macOS %s" % platform.mac_ver()[0]
    return "%s %s" % (platform.system(), platform.release())


def system_info():
    res = {"os": _os_name(), "hostname": platform.node()}
    b = _battery()
    res["battery"] = b if b else "none (desktop computer or no battery found)"
    up = _uptime_s()
    if up is not None:
        res["uptime"] = _human_duration(up)
    try:
        du = shutil.disk_usage(os.path.expanduser("~"))
        res["disk_free_gb"] = round(du.free / 1e9, 1)
        res["disk_total_gb"] = round(du.total / 1e9, 1)
    except OSError:
        pass
    if IS_LINUX:
        try:
            with open("/proc/meminfo") as f:
                mem = {ln.split(":")[0]: int(ln.split()[1]) for ln in f if ":" in ln}
            res["memory_free_gb"] = round(mem.get("MemAvailable", 0) / 1e6, 1)
            res["memory_total_gb"] = round(mem.get("MemTotal", 0) / 1e6, 1)
        except (OSError, ValueError, IndexError):
            pass
    return res
