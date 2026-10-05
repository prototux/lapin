import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from lapin_desktop import autostart, i18n
from lapin_desktop.config import Config


class ConfigTest(unittest.TestCase):
    def test_ids_generated_once(self):
        with tempfile.TemporaryDirectory() as d:
            c = Config(d)
            self.assertTrue(c["device_id"].startswith("desktop-"))
            self.assertGreaterEqual(len(c["token"]), 40)
            c2 = Config(d)
            self.assertEqual(c["device_id"], c2["device_id"])
            self.assertEqual(c["token"], c2["token"])
            self.assertEqual(stat.S_IMODE(os.stat(c.path).st_mode), 0o600)
            c.update({"owner": "jason", "bogus": 1})
            with open(c.path) as f:
                data = json.load(f)
            self.assertEqual(data["owner"], "jason")
            self.assertNotIn("bogus", data)
            self.assertEqual(c["server_url"], "ws://assistant.local:8765/v1/device")


class AutostartTest(unittest.TestCase):
    def test_xdg_entry(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            self.assertFalse(autostart.is_enabled())
            autostart.set_enabled(True)
            self.assertTrue(autostart.is_enabled())
            with open(os.path.join(d, "autostart", "lapin-desktop.desktop")) as f:
                text = f.read()
            self.assertIn("Exec=", text)
            self.assertIn("-m lapin_desktop", text)
            autostart.set_enabled(False)
            self.assertFalse(autostart.is_enabled())

    def test_quote(self):
        self.assertEqual(autostart.desktop_quote("/usr/bin/python3"), "/usr/bin/python3")
        self.assertEqual(autostart.desktop_quote("/home/a b/py"), '"/home/a b/py"')


class I18nTest(unittest.TestCase):
    def test_strings(self):
        for lang in ("fr", "en"):
            self.assertEqual(set(i18n.STRINGS[lang]), set(i18n.STRINGS["en"]), lang)
        i18n.setup("fr")
        self.assertEqual(i18n.tr("listening"), "J'écoute…")
        self.assertEqual(i18n.confirm_text("reboot the computer"), "Redémarrer l'ordinateur ?")
        i18n.setup("en")
        self.assertEqual(i18n.confirm_text("send “hi” to Marie"), "Send “hi” to Marie?")


if __name__ == "__main__":
    unittest.main()
