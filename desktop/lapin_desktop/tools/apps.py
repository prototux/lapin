"""open_app: finds an installed application by (fuzzy) name and starts it.

- Linux: .desktop files of the XDG data dirs (and flatpak/snap exports),
  matched on Name, GenericName and Keywords in any language; started with
  `gio launch`, `gtk-launch` or the parsed Exec line.
- Windows: the Start menu (Get-StartApps lists desktop and Store apps; the
  .lnk shortcuts as a fallback).
- macOS: the .app bundles of /Applications and friends, started with open -a.
"""

import difflib
import glob
import json
import os
import re
import shlex
import time

from .common import IS_MAC, IS_WIN, dry_run, fold, launch, run, which

# French/English words people use for common apps, added to the query
ALIASES = {
    "calculatrice": "calculator", "calculette": "calculator",
    "navigateur": "browser", "navigateur web": "web browser", "internet": "web browser",
    "explorateur de fichiers": "file manager", "gestionnaire de fichiers": "file manager",
    "fichiers": "files", "explorateur": "file explorer", "finder": "finder",
    "terminal": "terminal", "console": "terminal", "invite de commandes": "command prompt",
    "parametres": "settings", "reglages": "settings", "preferences systeme": "system settings",
    "editeur de texte": "text editor", "bloc notes": "notepad", "bloc-notes": "notepad",
    "musique": "music", "lecteur de musique": "music player", "lecteur video": "video player",
    "courrier": "mail", "messagerie": "mail", "e mail": "mail", "mail": "mail",
    "agenda": "calendar", "calendrier": "calendar", "photos": "photos", "images": "image viewer",
    "visionneuse": "image viewer", "moniteur systeme": "system monitor",
    "gestionnaire des taches": "task manager", "capture d ecran": "screenshot",
    "traitement de texte": "word processor", "tableur": "spreadsheet", "horloge": "clock",
    "meteo": "weather", "cartes": "maps", "plans": "maps", "contacts": "contacts",
    "magasin": "store", "logitheque": "software",
}
FILLER = {"l", "la", "le", "les", "un", "une", "app", "appli", "application", "logiciel", "programme",
          "the", "a", "an", "program", "de", "d"}


class App:
    def __init__(self, name, path, names, extra=(), launch_cmd=None):
        self.name = name                    # display name
        self.path = path                    # .desktop file, shortcut, bundle...
        self.names = [fold(n) for n in names if n]      # Name in all languages
        self.extra = [fold(n) for n in extra if n]      # GenericName, Keywords
        self.launch_cmd = launch_cmd

    def __repr__(self):
        return "App(%r)" % self.name


def _query_forms(query):
    q = fold(query)
    q2 = " ".join(w for w in q.split() if w not in FILLER) or q
    forms = []
    for f in (q2, q):
        for x in (f, fold(ALIASES.get(f, ""))):
            if x and x not in forms:
                forms.append(x)
    return forms


def _score(app, form):
    best = 0.0
    for n in app.names:
        if n == form:
            return 100.0
        nw = n.split()
        if n.startswith(form + " "):
            best = max(best, 90 - len(nw))
        elif form in nw:
            best = max(best, 85 - len(nw))
        elif form.startswith(n + " ") and len(n) > 3:
            best = max(best, 80)
        elif len(form) >= 4 and form in n:
            best = max(best, 70)
        r = difflib.SequenceMatcher(None, form, n).ratio()
        if r >= 0.75:
            best = max(best, 60 + 20 * (r - 0.75) / 0.25)
    for e in app.extra:
        if e == form:
            best = max(best, 72)
        elif len(form) >= 4 and (form in e.split() or (" " in form and form in e)):
            best = max(best, 55)
    return best


def match(apps, query):
    """[(score, app)] sorted, best first."""
    forms = _query_forms(query)
    scored = []
    for a in apps:
        s = max(_score(a, f) for f in forms)
        if s > 0:
            scored.append((s, a))
    scored.sort(key=lambda x: (-x[0], len(x[1].name)))
    return scored


def candidates(apps, query, n=5):
    q = _query_forms(query)[0]
    names = {a.name for a in apps}
    close = difflib.get_close_matches(q, [fold(x) for x in names], n=n, cutoff=0.62)
    by_fold = {fold(x): x for x in names}
    return [by_fold[c] for c in close]


# ---------------------------------------------------------------- Linux
def _xdg_app_dirs():
    home = os.path.expanduser("~")
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    dirs = [data_home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += [os.path.join(data_home, "flatpak", "exports", "share"), "/var/lib/flatpak/exports/share",
             "/var/lib/snapd/desktop"]
    out = []
    for d in dirs:
        p = os.path.join(d, "applications")
        if d and os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def parse_desktop(path):
    """The [Desktop Entry] group of a .desktop file, as a dict."""
    entry, inside = {}, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    if inside:
                        break
                    inside = line == "[Desktop Entry]"
                    continue
                if inside and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    entry[k.strip()] = v.strip()
    except OSError:
        pass
    return entry


def _localized(entry, key):
    """All values of a key: plain and every translation (Name, Name[fr]...)."""
    return [v for k, v in entry.items() if k == key or k.startswith(key + "[")]


def linux_apps():
    seen, apps = set(), []
    for d in _xdg_app_dirs():
        for path in sorted(glob.glob(os.path.join(d, "**", "*.desktop"), recursive=True)):
            desktop_id = os.path.relpath(path, d).replace(os.sep, "-")
            if desktop_id in seen:          # the first data dir wins (user overrides)
                continue
            seen.add(desktop_id)
            e = parse_desktop(path)
            if e.get("Type", "Application") != "Application" or e.get("NoDisplay") == "true" \
                    or e.get("Hidden") == "true" or not e.get("Name"):
                continue
            if e.get("TryExec") and not which(e["TryExec"]) and not os.path.exists(e["TryExec"]):
                continue
            keywords = [k for v in _localized(e, "Keywords") for k in v.split(";")]
            app = App(e["Name"], path, _localized(e, "Name") + [desktop_id[:-8].split(".")[-1]],
                      _localized(e, "GenericName") + keywords)
            app.desktop_id = desktop_id
            app.exec_line = e.get("Exec", "")
            app.terminal = e.get("Terminal") == "true"
            apps.append(app)
    return apps


def exec_command(exec_line):
    """The Exec line of a .desktop file without its field codes."""
    try:
        args = shlex.split(exec_line)
    except ValueError:
        args = exec_line.split()
    out = []
    for a in args:
        if re.fullmatch(r"%[fFuUdDnNickvm]", a):
            continue
        out.append(a.replace("%%", "%"))
    return out


def launch_linux(app):
    if which("gio"):
        return launch(["gio", "launch", app.path])
    if which("gtk-launch"):
        return launch(["gtk-launch", app.desktop_id])
    cmd = exec_command(app.exec_line)
    if not cmd:
        return {"error": "%s has no command to run" % app.name}
    if app.terminal:
        term = which("x-terminal-emulator", "gnome-terminal", "konsole", "xterm")
        if term:
            cmd = [term, "-e"] + cmd
    return launch(cmd)


# ---------------------------------------------------------------- Windows
_win_cache = [0.0, []]


def windows_apps():
    if time.time() - _win_cache[0] < 300 and _win_cache[1]:
        return _win_cache[1]
    apps = []
    code, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "Get-StartApps | ConvertTo-Json -Compress"], timeout=15, mutate=False)
    if code == 0 and out.strip():
        try:
            data = json.loads(out)
            for d in data if isinstance(data, list) else [data]:
                if d.get("Name") and d.get("AppID"):
                    apps.append(App(d["Name"], d["AppID"], [d["Name"]],
                                    launch_cmd=["explorer.exe", "shell:AppsFolder\\" + d["AppID"]]))
        except ValueError:
            pass
    if not apps:            # fallback: the Start menu shortcuts
        roots = [os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                 os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
        for r in roots:
            for p in glob.glob(os.path.join(r, "**", "*.lnk"), recursive=True):
                name = os.path.splitext(os.path.basename(p))[0]
                if "uninstall" in name.lower() or "désinstaller" in name.lower():
                    continue
                apps.append(App(name, p, [name]))
    _win_cache[:] = [time.time(), apps]
    return apps


def launch_windows(app):
    if app.launch_cmd:
        return launch(app.launch_cmd)
    if dry_run():
        return {"ok": True, "dry_run": "startfile %s" % app.path}
    try:
        os.startfile(app.path)          # noqa (Windows only)
    except OSError as e:
        return {"error": str(e)}
    return {"ok": True}


# ---------------------------------------------------------------- macOS
def mac_apps():
    roots = ["/Applications", "/Applications/Utilities", "/System/Applications",
             "/System/Applications/Utilities", os.path.expanduser("~/Applications")]
    apps, seen = [], set()
    for r in roots:
        for p in sorted(glob.glob(os.path.join(r, "*.app"))):
            name = os.path.basename(p)[:-4]
            if name in seen:
                continue
            seen.add(name)
            names = [name]
            # localized display name, when the bundle has a French one
            strings = os.path.join(p, "Contents", "Resources", "fr.lproj", "InfoPlist.strings")
            if os.path.exists(strings):
                try:
                    import plistlib
                    with open(strings, "rb") as f:
                        d = plistlib.load(f)
                    names += [d.get("CFBundleDisplayName", ""), d.get("CFBundleName", "")]
                except Exception:
                    pass
            apps.append(App(name, p, names))
    return apps


# ---------------------------------------------------------------- tool
def installed_apps():
    if IS_WIN:
        return windows_apps()
    if IS_MAC:
        return mac_apps()
    return linux_apps()


def find_app(query):
    """(app, [(score, app)...]) for a spoken/typed app name."""
    apps = installed_apps()
    ranked = match(apps, query)
    if ranked and ranked[0][0] >= 55:
        return ranked[0][1], ranked, apps
    return None, ranked, apps


def open_app(app=""):
    if not str(app).strip():
        return {"error": "no application name given"}
    found, ranked, apps = find_app(app)
    if not found:
        close = [a.name for s, a in ranked[:5] if s > 0] or candidates(apps, app)
        res = {"error": "no installed application matches %r" % app}
        if close:
            res["candidates"] = close
        return res
    if IS_WIN:
        res = launch_windows(found)
    elif IS_MAC:
        res = launch(["open", "-a", found.path])
    else:
        res = launch_linux(found)
    if "error" not in res:
        res["app"] = found.name
    return res
