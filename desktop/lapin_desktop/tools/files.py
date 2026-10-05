"""open_url, open_folder, screenshot and the clipboard."""

import os
import re
import time

from .common import IS_LINUX, dry_run, fold, in_main_thread, open_with_system, run, which

MAX_CLIPBOARD = 2000        # characters returned by read_clipboard


def open_url(url=""):
    url = str(url).strip()
    if not url:
        return {"error": "no URL given"}
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        if " " in url or "." not in url:
            return {"error": "not a URL: %r" % url}
        url = "https://" + url
    scheme = url.split(":", 1)[0].lower()
    if scheme not in ("http", "https", "mailto", "tel", "ftp", "sftp", "smb", "magnet", "spotify",
                      "steam", "zoommtg", "msteams", "slack", "discord", "tg"):
        return {"error": "refusing to open %s: links" % scheme}
    res = open_with_system(url)
    if "error" not in res:
        res["url"] = url
    return res


# folder names people say -> Qt standard location
FOLDERS = {
    "documents": "Documents", "document": "Documents", "mes documents": "Documents",
    "downloads": "Download", "download": "Download", "telechargements": "Download",
    "telechargement": "Download", "telecharges": "Download",
    "pictures": "Pictures", "images": "Pictures", "photos": "Pictures", "mes images": "Pictures",
    "music": "Music", "musique": "Music", "ma musique": "Music",
    "videos": "Movies", "video": "Movies", "movies": "Movies", "films": "Movies",
    "desktop": "Desktop", "bureau": "Desktop",
    "home": "Home", "personal": "Home", "personnel": "Home", "dossier personnel": "Home", "maison": "Home",
    "accueil": "Home", "user": "Home", "utilisateur": "Home",
    "temp": "Temp", "temporary": "Temp", "temporaire": "Temp",
}


def standard_dir(key):
    from PySide6.QtCore import QStandardPaths
    loc = {"Documents": QStandardPaths.DocumentsLocation, "Download": QStandardPaths.DownloadLocation,
           "Pictures": QStandardPaths.PicturesLocation, "Music": QStandardPaths.MusicLocation,
           "Movies": QStandardPaths.MoviesLocation, "Desktop": QStandardPaths.DesktopLocation,
           "Home": QStandardPaths.HomeLocation, "Temp": QStandardPaths.TempLocation}[key]
    return QStandardPaths.writableLocation(loc)


def resolve_folder(name):
    raw = str(name or "").strip()
    if not raw:
        return None
    p = os.path.expanduser(raw)
    if os.path.isabs(p) and os.path.isdir(p):
        return p
    words = [w for w in fold(raw).split() if w not in ("le", "la", "les", "dossier", "repertoire", "folder",
                                                         "directory", "mes", "my", "the", "de", "d", "des")]
    key = " ".join(words)
    std = FOLDERS.get(key) or FOLDERS.get(fold(raw))
    if std:
        return standard_dir(std)
    # a folder directly in the home or in the standard folders, by name
    home = os.path.expanduser("~")
    roots = [home] + [standard_dir(k) for k in ("Documents", "Desktop", "Download")]
    for r in roots:
        try:
            for entry in sorted(os.listdir(r)):
                if not entry.startswith(".") and fold(entry) == key and os.path.isdir(os.path.join(r, entry)):
                    return os.path.join(r, entry)
        except OSError:
            pass
    return None


def open_folder(name=""):
    path = resolve_folder(name)
    if not path:
        return {"error": "no folder named %r" % name,
                "candidates": ["Documents", "Downloads", "Pictures", "Music", "Videos", "Desktop", "home"]}
    if not os.path.isdir(path):
        return {"error": "the folder %s does not exist" % path}
    res = open_with_system(path)
    if "error" not in res:
        res["path"] = path
    return res


# ---------------------------------------------------------------- clipboard
def copy_to_clipboard(text=""):
    text = str(text)
    if dry_run():
        return {"ok": True, "dry_run": "copy %d characters" % len(text)}

    def do():
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
    in_main_thread(do)
    return {"ok": True, "characters": len(text)}


def read_clipboard():
    def do():
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.clipboard().text() or ""
    text = in_main_thread(do)
    if not text:
        return {"ok": True, "text": "", "note": "the clipboard is empty or holds no text"}
    res = {"ok": True, "text": text[:MAX_CLIPBOARD]}
    if len(text) > MAX_CLIPBOARD:
        res["truncated"] = True
        res["total_characters"] = len(text)
    return res


# ---------------------------------------------------------------- screenshot
_hide_overlay = None        # set by the app: callable(bool) hiding the overlay during the grab


def set_overlay_hider(fn):
    global _hide_overlay
    _hide_overlay = fn


def _wayland():
    return IS_LINUX and (os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"))


def screenshot(target_dir=""):
    folder = target_dir or standard_dir("Pictures") or os.path.expanduser("~")
    path = os.path.join(folder, time.strftime("Screenshot %Y-%m-%d %H-%M-%S.png"))
    if dry_run():
        return {"ok": True, "dry_run": "save %s" % path}
    os.makedirs(folder, exist_ok=True)
    if _wayland():
        # Qt can't grab the screen on Wayland: use the desktop's tool
        for cmd in (["grim", path], ["gnome-screenshot", "-f", path], ["spectacle", "-b", "-n", "-f", "-o", path]):
            if which(cmd[0]):
                if _hide_overlay:
                    in_main_thread(lambda: _hide_overlay(True))
                code, out = run(cmd, timeout=15)
                if _hide_overlay:
                    in_main_thread(lambda: _hide_overlay(False))
                if code == 0 and os.path.exists(path):
                    return {"ok": True, "path": path}
        return {"error": "screen capture is not available on this Wayland desktop (install grim, "
                         "gnome-screenshot or spectacle)"}

    def grab():
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        if _hide_overlay:
            _hide_overlay(True)
        try:
            screens = QGuiApplication.screens()
            if not screens:
                raise RuntimeError("no screen")
            area = QRect()
            for s in screens:
                area = area.united(s.geometry())
            img = QImage(area.size(), QImage.Format_RGB32)
            img.fill(0)
            p = QPainter(img)
            for s in screens:
                g = s.geometry()
                pix = s.grabWindow(0)
                p.drawPixmap(g.x() - area.x(), g.y() - area.y(), g.width(), g.height(), pix)
            p.end()
            if not img.save(path, "PNG"):
                raise RuntimeError("could not write %s" % path)
            return img.width(), img.height()
        finally:
            if _hide_overlay:
                _hide_overlay(False)
    w, h = in_main_thread(grab)
    return {"ok": True, "path": path, "size": "%dx%d" % (w, h)}
