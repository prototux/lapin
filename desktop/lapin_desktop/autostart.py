"""Start at login: XDG autostart entry (Linux), the Run registry key
(Windows), a LaunchAgent (macOS)."""

import os
import plistlib
import sys

NAME = "lapin-desktop"
LABEL = "net.lapin.desktop"


def command():
    """How to start this copy of the app: its launcher when started through
    one (works from any folder), the package's own way for the released
    builds, else the interpreter."""
    launcher = os.environ.get("LAPIN_LAUNCHER")
    if launcher and os.path.exists(launcher):
        return [launcher]
    if os.environ.get("FLATPAK_ID") and os.path.exists("/.flatpak-info"):
        return ["flatpak", "run", os.environ["FLATPAK_ID"]]
    if getattr(sys, "frozen", False):
        return [sys.executable]         # the Windows and macOS builds (PyInstaller)
    exe = sys.executable
    if sys.platform.startswith("win"):
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w             # no console window
    return [exe, "-m", "lapin_desktop"]


def desktop_quote(arg):
    """Quoting of the Exec key (Desktop Entry spec): double quotes, with
    backslash before " ` $ and backslash."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"%s"' % "".join("\\" + c if c in '"`$\\' else c for c in arg)


def _xdg_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    if os.path.exists("/.flatpak-info"):
        base = os.path.join(os.path.expanduser("~"), ".config")     # the host's, not the sandbox's
    return os.path.join(base, "autostart", NAME + ".desktop")


def _mac_path():
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", LABEL + ".plist")


def is_enabled():
    if sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, "Lapin")
            return True
        except OSError:
            return False
    return os.path.exists(_mac_path() if sys.platform == "darwin" else _xdg_path())


def set_enabled(on):
    cmd = command()
    if sys.platform.startswith("win"):
        import subprocess
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, "Lapin", 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(k, "Lapin")
                except OSError:
                    pass
        return
    path = _mac_path() if sys.platform == "darwin" else _xdg_path()
    if not on:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if sys.platform == "darwin":
        with open(path, "wb") as f:
            plistlib.dump({"Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True,
                           "ProcessType": "Interactive"}, f)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Lapin\nComment=Lapin voice assistant\n"
                "Exec=%s\nIcon=audio-input-microphone\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
                "X-GNOME-Autostart-Delay=5\n" % " ".join(desktop_quote(c) for c in cmd))
