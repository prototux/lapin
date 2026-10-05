"""Renders the overlay in each state to PNG files (offscreen), to check the
layout:  QT_QPA_PLATFORM=offscreen python tests/snapshots.py OUT_DIR"""

import os
import sys

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lapin_desktop import i18n  # noqa: E402
from lapin_desktop.overlay import Overlay  # noqa: E402


def on_wallpaper(widget, path, dark):
    img = widget.grab()
    bg = QPixmap(img.width() + 80, img.height() + 60)
    p = QPainter(bg)
    g = QLinearGradient(QPointF(0, 0), QPointF(bg.width(), bg.height()))
    g.setColorAt(0, QColor("#1d2b4f" if dark else "#9ec5ff"))
    g.setColorAt(1, QColor("#3b1d4f" if dark else "#f5c6e0"))
    p.fillRect(bg.rect(), g)
    p.drawPixmap(40, 30, img)
    p.end()
    bg.save(path)


def main(out):
    app = QApplication(sys.argv[:1])
    os.makedirs(out, exist_ok=True)
    for lang in ("fr", "en"):
        i18n.setup(lang)
        for dark in (True, False):
            o = Overlay(level_fn=lambda: 0.7, out_level_fn=lambda: 0.5)
            o.apply_theme(dark)
            o.retranslate()
            o.show()
            o.setWindowOpacity(1)
            name = "%s-%s" % (lang, "dark" if dark else "light")

            def snap(state):
                for _ in range(20):
                    o.orb._tick()
                app.processEvents()
                o._fit()
                app.processEvents()
                on_wallpaper(o, os.path.join(out, "%s-%s.png" % (name, state)), dark)

            o.new_turn("button")
            o.set_state("listening")
            o.set_transcript("Quelle heure est" if lang == "fr" else "What time is")
            snap("1-listening")
            o.set_state("thinking")
            o.set_transcript("Quelle heure est-il ?" if lang == "fr" else "What time is it?", True)
            snap("2-thinking")
            o.set_state("speaking")
            o.set_reply("Il est 18 heures 43." if lang == "fr" else "It's 6:43 PM.")
            snap("3-speaking")
            o.new_turn("text")
            o.set_transcript("Éteins l'ordinateur" if lang == "fr" else "Shut down the computer")
            o.set_state("idle")
            o.set_reply("Je vais éteindre l'ordinateur, c'est bien ça ?" if lang == "fr"
                        else "I'll shut down the computer, is that right?")
            o.show_confirm("shutdown the computer")
            snap("4-confirm")
            o.hide_confirm()
            o.new_turn("text")
            o.set_transcript("Ouvre le dossier Téléchargements" if lang == "fr" else "Open the Downloads folder")
            o.set_reply("", "done")
            snap("5-done")
            o.new_turn("button")
            o.set_notice(i18n.tr("no_speech"))
            snap("6-notice")
            o.new_turn("text")
            o.set_state("idle")
            o.set_transcript("Raconte-moi l'histoire de Paris" if lang == "fr" else "Tell me about Paris")
            o.set_reply(" ".join(["Paris est la capitale de la France, sur la Seine, fondée par les Parisii "
                                  "au IIIe siècle avant notre ère."] * 8))
            snap("7-long")
            o.close()
    print("written to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "snapshots")
