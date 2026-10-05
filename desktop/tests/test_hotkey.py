import unittest

from lapin_desktop.hotkey import qt_to_pynput


class HotkeyTest(unittest.TestCase):
    def test_conversion(self):
        self.assertEqual(qt_to_pynput("Ctrl+Alt+Space", mac=False), "<ctrl>+<alt>+<space>")
        self.assertEqual(qt_to_pynput("Meta+A", mac=False), "<cmd>+a")
        self.assertEqual(qt_to_pynput("Ctrl+Shift+F12", mac=False), "<ctrl>+<shift>+<f12>")
        self.assertEqual(qt_to_pynput("Alt+Return", mac=False), "<alt>+<enter>")
        self.assertEqual(qt_to_pynput("Ctrl+Alt+Space", mac=True), "<cmd>+<alt>+<space>")
        self.assertEqual(qt_to_pynput("Meta+Space", mac=True), "<ctrl>+<space>")

    def test_bad(self):
        for seq in ("", "Hyper+X", "Ctrl+Volume Up"):
            with self.assertRaises(ValueError):
                qt_to_pynput(seq, mac=False)


if __name__ == "__main__":
    unittest.main()
