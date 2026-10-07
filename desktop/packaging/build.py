#!/usr/bin/env python3
"""Builds the packaged desktop app for this OS, into desktop/dist:

- Windows: lapin-desktop-<version>-windows-x64-setup.exe (Inno Setup, installs for
  the current user, no admin rights) and lapin-desktop-<version>-windows-x64.zip
  (portable);
- macOS: lapin-desktop-<version>-macos-<arch>.dmg.

It needs the app's dependencies, PyInstaller and Pillow, and on Windows Inno
Setup 6 (iscc):

    pip install -r requirements.txt pyinstaller pillow
    python packaging/build.py

The Linux build is the Flatpak (packaging/flatpak)."""

import glob
import os
import platform
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, ROOT)


def render_icons():
    """lapin.svg -> PNG (Qt), then .ico and .icns (Pillow)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (keeps Qt alive)
    os.makedirs(BUILD, exist_ok=True)
    img = QImage(1024, 1024, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    QSvgRenderer(os.path.join(HERE, "lapin.svg")).render(p)
    p.end()
    png = os.path.join(BUILD, "lapin.png")
    img.save(png)
    im = Image.open(png)
    im.save(os.path.join(BUILD, "lapin.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    im.save(os.path.join(BUILD, "lapin.icns"))


def pyinstaller():
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
                    os.path.join(HERE, "lapin.spec")], check=True)


def iscc():
    found = shutil.which("iscc")
    for d in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        found = found or (glob.glob(os.path.join(d, "Inno Setup*", "ISCC.exe")) or [None])[0]
    if not found:
        sys.exit("Inno Setup 6 (ISCC.exe) not found: https://jrsoftware.org/isinfo.php")
    return found


def windows(version):
    app_dir = os.path.join(BUILD, "dist", "Lapin")
    name = "lapin-desktop-%s-windows-x64" % version
    shutil.make_archive(os.path.join(DIST, name), "zip", os.path.dirname(app_dir), "Lapin")
    subprocess.run([iscc(), "/Q", "/DAppVersion=" + version, "/DSourceDir=" + app_dir,
                    "/DIconFile=" + os.path.join(BUILD, "lapin.ico"), "/DOutputDir=" + DIST,
                    "/DOutputName=" + name + "-setup", os.path.join(HERE, "windows", "lapin.iss")], check=True)


def macos(version):
    arch = {"x86_64": "x64"}.get(platform.machine(), platform.machine())
    stage = os.path.join(BUILD, "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    # symlinks=True keeps the frameworks' links (and the ad-hoc signature) intact
    shutil.copytree(os.path.join(BUILD, "dist", "Lapin.app"), os.path.join(stage, "Lapin.app"), symlinks=True)
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    dmg = os.path.join(DIST, "lapin-desktop-%s-macos-%s.dmg" % (version, arch))
    if os.path.exists(dmg):
        os.remove(dmg)
    cmd = ["hdiutil", "create", "-volname", "Lapin", "-srcfolder", stage, "-fs", "HFS+", "-format", "UDZO", dmg]
    for _ in range(3):              # hdiutil sometimes fails with "Resource busy" on CI machines
        if subprocess.run(cmd).returncode == 0:
            return
        time.sleep(10)
    sys.exit("hdiutil could not create %s" % dmg)


def main():
    from lapin_desktop import __version__
    os.makedirs(DIST, exist_ok=True)
    render_icons()
    pyinstaller()
    if sys.platform.startswith("win"):
        windows(__version__)
    elif sys.platform == "darwin":
        macos(__version__)
    else:
        print("built %s (Linux packages are made with the Flatpak manifest)" % os.path.join(BUILD, "dist"))
        return
    for f in sorted(os.listdir(DIST)):
        print("dist/" + f)


if __name__ == "__main__":
    main()
