import os
import re
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LAPIN_DRY_RUN"] = "1"           # never launch, lock or change anything here

from PySide6.QtWidgets import QApplication  # noqa: E402

from lapin_desktop.tools import Registry, apps, files, system  # noqa: E402

DESKTOP = """[Desktop Entry]
Type=Application
Name={name}
{extra}
Exec={exec}
"""


def write_app(d, fid, name, exec_="true", extra=""):
    with open(os.path.join(d, fid), "w") as f:
        f.write(DESKTOP.format(name=name, exec=exec_, extra=extra))


class AppsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = os.path.join(cls.tmp.name, "applications")
        os.makedirs(os.path.join(d, "sub"))
        write_app(d, "org.gnome.Calculator.desktop", "Calculator",
                  extra="Name[fr]=Calculatrice\nGenericName=Calculator\nKeywords=calculation;arithmetic;")
        write_app(d, "firefox.desktop", "Firefox", "firefox %u",
                  extra="GenericName=Web Browser\nGenericName[fr]=Navigateur Web\nKeywords=Internet;WWW;")
        write_app(d, "org.gnome.Nautilus.desktop", "Files",
                  extra="Name[fr]=Fichiers\nGenericName=File Manager\nKeywords=folder;explorer;")
        write_app(d, "hidden.desktop", "Secret", extra="NoDisplay=true")
        write_app(d, "spotify.desktop", "Spotify", "spotify --uri=%U")
        write_app(os.path.join(d, "sub"), "vlc.desktop", "VLC media player",
                  extra="GenericName=Media player\nGenericName[fr]=Lecteur multimédia")
        cls.env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": cls.tmp.name, "XDG_DATA_DIRS": "/nonexistent"})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def found(self, query):
        res = apps.open_app(query)
        return res.get("app"), res

    def test_names_and_languages(self):
        cases = {"calculatrice": "Calculator", "la calculatrice": "Calculator", "Calculator": "Calculator",
                 "firefox": "Firefox", "navigateur": "Firefox", "le navigateur web": "Firefox",
                 "fichiers": "Files", "gestionnaire de fichiers": "Files", "spotifi": "Spotify",
                 "VLC": "VLC media player", "lecteur multimédia": "VLC media player"}
        for q, want in cases.items():
            self.assertEqual(self.found(q)[0], want, q)

    def test_hidden_and_unknown(self):
        name, res = self.found("Secret")
        self.assertIsNone(name)
        self.assertIn("error", res)
        name, res = self.found("Photoshop")
        self.assertIn("error", res)

    def test_candidates(self):
        res = apps.open_app("spotfy music")
        self.assertTrue(res.get("app") == "Spotify" or "Spotify" in res.get("candidates", []), res)

    def test_desktop_id_from_subdir(self):
        a = [x for x in apps.linux_apps() if x.name == "VLC media player"][0]
        self.assertEqual(a.desktop_id, "sub-vlc.desktop")

    def test_exec_field_codes(self):
        self.assertEqual(apps.exec_command('spotify --uri=%U'), ["spotify", "--uri=%U"])
        self.assertEqual(apps.exec_command('firefox %u'), ["firefox"])
        self.assertEqual(apps.exec_command('"/opt/My App/run" --x %F %%'), ["/opt/My App/run", "--x", "%"])

    def test_dry_run_command(self):
        res = apps.open_app("firefox")
        self.assertTrue(res["ok"])
        self.assertIn("firefox.desktop", res["dry_run"])


class FilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_folders(self):
        self.assertEqual(files.resolve_folder("Téléchargements"), files.standard_dir("Download"))
        self.assertEqual(files.resolve_folder("le dossier Documents"), files.standard_dir("Documents"))
        self.assertEqual(files.resolve_folder("bureau"), files.standard_dir("Desktop"))
        self.assertEqual(files.resolve_folder("home"), os.path.expanduser("~"))
        self.assertIsNone(files.resolve_folder("no such folder xyz"))
        self.assertIn("error", files.open_folder("no such folder xyz"))

    def test_urls(self):
        self.assertEqual(files.open_url("wikipedia.org")["url"], "https://wikipedia.org")
        self.assertIn("error", files.open_url("file:///etc/passwd"))
        self.assertIn("error", files.open_url("javascript:alert(1)"))
        self.assertIn("error", files.open_url("hello world"))
        self.assertTrue(files.open_url("https://example.com/a?b=c")["ok"])

    def test_clipboard_roundtrip(self):
        with mock.patch.dict(os.environ, {"LAPIN_DRY_RUN": "0"}):
            self.assertTrue(files.copy_to_clipboard("bonjour")["ok"])
            self.assertEqual(files.read_clipboard()["text"], "bonjour")
            files.copy_to_clipboard("x" * 5000)
            res = files.read_clipboard()
            self.assertEqual(len(res["text"]), files.MAX_CLIPBOARD)
            self.assertTrue(res["truncated"])

    def test_screenshot_writes_png(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LAPIN_DRY_RUN": "0"}):
            res = files.screenshot(d)
            self.assertTrue(res.get("ok"), res)
            self.assertTrue(os.path.exists(res["path"]))
            with open(res["path"], "rb") as f:
                self.assertEqual(f.read(4), b"\x89PNG")


class SystemTest(unittest.TestCase):
    def test_power_dry(self):
        self.assertIn("systemctl poweroff", system.power("shutdown")["dry_run"])
        self.assertIn("systemctl reboot", system.power("reboot")["dry_run"])
        self.assertIn("error", system.power("explode"))

    def test_lock_dry(self):
        res = system.lock_screen()
        self.assertTrue(res.get("ok") or "error" in res)

    def test_volume_dry(self):
        res = system.computer_volume(level=30)
        self.assertEqual(res.get("level"), 30)
        self.assertIn("error", system.computer_volume())
        self.assertIn("error", system.computer_volume(level="loud"))
        with mock.patch.object(system, "_get_volume", return_value=(95, False)):
            self.assertEqual(system.computer_volume(change=10)["level"], 100)
            self.assertEqual(system.computer_volume(change=-20)["level"], 75)

    def test_media_bad_action(self):
        self.assertIn("error", system.computer_media("explode"))

    def test_system_info(self):
        res = system.system_info()
        self.assertIn("os", res)
        self.assertIn("disk_free_gb", res)


class RegistryTest(unittest.TestCase):
    def test_specs(self):
        specs = Registry().specs()
        names = [s["name"] for s in specs]
        self.assertEqual(len(names), len(set(names)))
        server_tools = {"media_control", "set_volume", "set_timer", "play_radio", "stop_media", "send_message"}
        for s in specs:
            self.assertRegex(s["name"], r"^[a-z][a-z0-9_]{1,40}$")
            self.assertNotIn(s["name"], server_tools)
            self.assertLessEqual(len(s["description"]), 500)
            self.assertEqual(s["parameters"]["type"], "object")
        power = [s for s in specs if s["name"] == "power"][0]
        self.assertTrue(power["confirm"])
        self.assertIn("{action}", power["summary"])
        silent = {s["name"] for s in specs if s["silent"]}
        self.assertTrue({"open_app", "open_url", "open_folder", "computer_volume", "lock_screen",
                         "copy_to_clipboard"} <= silent)

    def test_call_errors(self):
        r = Registry()
        self.assertIn("error", r.call("nope", {}))
        self.assertIn("error", r.call("open_app", {}))
        self.assertTrue(re.match("no ", r.call("open_app", {"app": "  "})["error"]))


if __name__ == "__main__":
    unittest.main()


class MprisTest(unittest.TestCase):
    """computer_media against a fake MPRIS player registered on the session bus."""

    def test_pause(self):
        from PySide6.QtCore import ClassInfo, Property, QObject, Slot
        from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection
        QApplication.instance() or QApplication([])
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            self.skipTest("no D-Bus session bus")

        @ClassInfo({"D-Bus Interface": "org.mpris.MediaPlayer2.Player"})
        class Player(QDBusAbstractAdaptor):
            calls = []

            def _status(self):
                return "Playing"
            PlaybackStatus = Property(str, _status)

            @Slot()
            def Pause(self):
                self.calls.append("Pause")

        root = QObject()
        Player(root)
        name = "org.mpris.MediaPlayer2.lapintest%d" % os.getpid()
        self.assertTrue(bus.registerService(name))
        try:
            self.assertTrue(bus.registerObject("/org/mpris/MediaPlayer2", root))
            players = [p for p in system._mpris_players() if p[0] == name]
            self.assertEqual(players[0][2], "Playing")
            # only ever talk to the fake player, even if a real one is running
            with mock.patch.object(system, "_mpris_players", return_value=players), \
                    mock.patch.object(system, "which", return_value=None), \
                    mock.patch.dict(os.environ, {"LAPIN_DRY_RUN": "0"}):
                res = system.computer_media("pause")
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(Player.calls, ["Pause"])
        finally:
            bus.unregisterObject("/org/mpris/MediaPlayer2")
            bus.unregisterService(name)
