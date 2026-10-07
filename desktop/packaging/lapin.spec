# PyInstaller spec of the desktop app: Lapin.exe and its folder (Windows),
# Lapin.app (macOS). Run it through build.py, which renders the icon first
# and then makes the installer or the disk image.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from lapin_desktop import __version__  # noqa: E402

MAC = sys.platform == "darwin"
# pynput picks its backend at run time: keep the one of this platform only
OTHERS = ("xorg", "uinput", "win32") if MAC else ("xorg", "uinput", "darwin")
pynput = [m for m in collect_submodules("pynput") if not any(o in m.rsplit(".", 1)[-1] for o in OTHERS)]

a = Analysis(
    [os.path.join(HERE, "lapin_entry.py")],
    pathex=[ROOT],
    hiddenimports=pynput + collect_submodules("lapin_desktop"),
    excludes=["tkinter", "unittest", "pydoc"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Lapin",
    console=False,
    icon=os.path.join(HERE, "build", "lapin.icns" if MAC else "lapin.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Lapin")
if MAC:
    app = BUNDLE(
        coll,
        name="Lapin.app",
        icon=os.path.join(HERE, "build", "lapin.icns"),
        bundle_identifier="net.prototux.lapin",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "Lapin",
            "CFBundleShortVersionString": __version__,
            "LSUIElement": True,                # a menu bar app: no Dock icon
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Lapin listens to your requests after you press its shortcut.",
            "NSAppleEventsUsageDescription": "Lapin controls Music, Spotify and System Events when you ask it to.",
        },
    )
