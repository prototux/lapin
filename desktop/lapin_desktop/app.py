"""The desktop app: tray icon, overlay, global hotkey and single instance,
wired to the assistant client."""

import logging
import signal

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import autostart, i18n
from .core import Assistant
from .hotkey import GlobalHotkey
from .i18n import tr
from .single import SingleInstance
from .tools import common, files

log = logging.getLogger("app")

ICON_COLORS = {"online": ("#7C5CFF", "#2EC5FF"), "pending": ("#FF9F0A", "#FFCC00"),
               "error": ("#FF453A", "#FF9F0A")}


def make_icon(status="online", size=64):
    """A round gradient badge with two rabbit ears (Lapin)."""
    a, b = ICON_COLORS.get(status, ("#8E8E93", "#C7C7CC"))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64
    g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    g.setColorAt(0, QColor(a))
    g.setColorAt(1, QColor(b))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    # ears, then the head
    p.drawRoundedRect(QRectF(17 * s, 2 * s, 10 * s, 30 * s), 5 * s, 5 * s)
    p.drawRoundedRect(QRectF(37 * s, 2 * s, 10 * s, 30 * s), 5 * s, 5 * s)
    p.drawEllipse(QRectF(8 * s, 22 * s, 48 * s, 40 * s))
    p.setBrush(QColor(255, 255, 255, 230))
    p.drawEllipse(QPointF(24 * s, 40 * s), 3.6 * s, 3.6 * s)
    p.drawEllipse(QPointF(40 * s, 40 * s), 3.6 * s, 3.6 * s)
    p.end()
    return QIcon(pix)


class DesktopApp:
    def __init__(self, qapp, cfg, headless=False):
        self.qapp = qapp
        self.cfg = cfg
        self.headless = headless
        self.assistant = None
        self.overlay = None
        self.tray = None
        self.settings_dialog = None
        self.hotkey = GlobalHotkey()
        self.single = SingleInstance()

    def start(self):
        if not self.single.listen():
            log.warning("could not open the single-instance socket; --activate won't reach this instance")
        self.single.command.connect(self.on_command)
        common.setup_main_thread()
        self.qapp.setWindowIcon(make_icon())

        a = self.assistant = Assistant(self.cfg)
        a.status_changed.connect(self.on_status)
        a.notice.connect(lambda s: log.info("notice: %s", s))
        a.state_changed.connect(lambda s: log.debug("state: %s", s))
        a.transcript.connect(lambda t, f: f and log.info("heard: %s", t))
        a.reply.connect(lambda t, ack: log.info("reply: %s", t or ack))

        if not self.headless:
            self._build_ui()
        else:
            a.follow_up_ok = lambda: True

        self.hotkey.triggered.connect(self.activate)
        self.hotkey.set(self.cfg["hotkey"])
        if self.cfg["autostart"] != autostart.is_enabled():
            try:
                autostart.set_enabled(self.cfg["autostart"])
            except OSError as e:
                log.warning("autostart: %s", e)

        # Ctrl+C / SIGTERM: quit cleanly (the timer lets Python see the signal)
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        self._sig_timer = QTimer()
        self._sig_timer.timeout.connect(lambda: None)
        self._sig_timer.start(300)

        a.start()
        log.info("Lapin desktop started (device %s, server %s)", self.cfg["device_id"], self.cfg["server_url"])
        return True

    def _build_ui(self):
        from .overlay import Overlay
        a = self.assistant
        o = self.overlay = Overlay(level_fn=lambda: a.mic.level, out_level_fn=lambda: a.mixer.level)
        o.last_tool = lambda: a.last_tool
        a.follow_up_ok = lambda: o.isVisible() and not o.hiding
        a.state_changed.connect(o.set_state)
        a.transcript.connect(o.set_transcript)
        a.reply.connect(o.set_reply)
        a.confirm_requested.connect(lambda summary, tool: o.show_confirm(summary))
        a.confirm_cleared.connect(o.hide_confirm)
        a.notice.connect(self.on_notice)
        a.turn_started.connect(o.new_turn)
        a.finished.connect(lambda follow_up: o.schedule_autohide())
        a.attention.connect(o.present)
        o.dismissed.connect(self.dismiss)
        o.text_submitted.connect(a.send_text)
        o.mic_clicked.connect(a.toggle)
        o.confirm_answered.connect(a.answer_confirm)
        o.typing_started.connect(a.cancel)
        files.set_overlay_hider(o.hide_for_grab)

        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(make_icon("connecting"))
            self.tray.setToolTip(tr("tray_tip"))
            self.tray.activated.connect(self.on_tray)
            self._build_menu()
            self.tray.show()
        else:
            log.info("no system tray: use the hotkey or --activate / --settings")

    def _build_menu(self):
        m = QMenu()
        self.status_action = QAction(tr(self.assistant.status) if self.assistant else "", m)
        self.status_action.setEnabled(False)
        m.addAction(self.status_action)
        m.addSeparator()
        m.addAction(tr("talk"), self.activate)
        self.stop_action = m.addAction(tr("stop_audio"), self.assistant.stop_audio)
        m.addAction(tr("settings"), self.show_settings)
        m.addSeparator()
        m.addAction(tr("quit"), self.quit)
        m.aboutToShow.connect(lambda: self.stop_action.setVisible(self.assistant.mixer.busy(("media", "tts",
                                                                                              "alarm"))))
        self.menu = m
        self.tray.setContextMenu(m)

    # ------------------------------------------------------------ events
    def on_command(self, cmd):
        if cmd == "quit":
            self.quit()
        elif cmd == "settings":
            self.show_settings()
        else:
            self.activate()

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.activate()

    def on_status(self, status, detail):
        log.info("server: %s %s", status, detail)
        if self.tray:
            self.tray.setIcon(make_icon(status))
            self.tray.setToolTip("%s — %s" % (tr("tray_tip"), tr(status) if status in
                                              ("online", "pending", "offline", "connecting", "error") else status))
            self.status_action.setText(tr(status) if status != "error" else "%s: %s" % (tr("error"), detail))

    def on_notice(self, text):
        o = self.overlay
        if not o.isVisible():
            o.present()
        o.set_notice(text, error=self.assistant.state == "idle" and text != tr("no_speech"))

    def activate(self):
        """Hotkey, tray click, --activate: show the card and listen; again
        while listening ends the turn; while answering, interrupts."""
        a = self.assistant
        if self.headless or not self.overlay:
            a.toggle()
            return
        o = self.overlay
        if not o.isVisible() or o.hiding:
            o.present()
            a.start_listening("button")
        else:
            o.present()
            a.toggle()

    def activate_later(self):
        # let the link connect first when started by --activate
        def go(tries=[0]):
            if self.assistant.online or tries[0] > 25:
                self.activate()
            else:
                tries[0] += 1
                QTimer.singleShot(200, go)
        QTimer.singleShot(200, go)

    def dismiss(self):
        self.assistant.cancel()
        if self.overlay:
            self.overlay.dismiss_animated()

    def show_settings(self):
        from .settings import SettingsDialog
        if self.settings_dialog and self.settings_dialog.isVisible():
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        d = self.settings_dialog = SettingsDialog(self.cfg, self.assistant, self.hotkey.error)
        d.setWindowIcon(make_icon())
        d.accepted.connect(lambda: self.apply_settings(d.changes()))
        d.show()
        d.raise_()
        d.activateWindow()

    def apply_settings(self, ch):
        old = dict(self.cfg.data)
        self.cfg.update(ch)
        if ch["hotkey"] != old["hotkey"] or not self.hotkey.listener:
            self.hotkey.set(ch["hotkey"])
        try:
            autostart.set_enabled(ch["autostart"])
        except OSError as e:
            log.warning("autostart: %s", e)
        if ch["language"] != old["language"]:
            i18n.setup(ch["language"])
            if self.overlay:
                self.overlay.retranslate()
            if self.tray:
                self._build_menu()
        if any(ch[k] != old[k] for k in ("server_url", "name", "owner")):
            self.assistant.reconnect()

    def quit(self):
        self.qapp.quit()

    def shutdown(self):
        self.hotkey.stop()
        if self.assistant:
            self.assistant.shutdown()
        if self.tray:
            self.tray.hide()
