"""Settings dialog: server, device name, owner, shortcut, start at login,
spoken answers to typed requests, language; shows the pairing status."""

import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QKeySequenceEdit, QVBoxLayout)

from . import autostart
from .hotkey import is_wayland
from .i18n import tr

STATUS_COLORS = {"online": "#34C759", "pending": "#FF9F0A", "error": "#FF453A", "offline": "#8E8E93",
                 "connecting": "#8E8E93"}


def activate_command():
    import shlex
    cmd = autostart.command() + ["--activate"]
    if sys.platform.startswith("win"):
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


class SettingsDialog(QDialog):
    def __init__(self, cfg, assistant, hotkey_error="", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.assistant = assistant
        self.setWindowTitle(tr("settings_title"))
        self.setMinimumWidth(580)

        self.url = QLineEdit(cfg["server_url"])
        self.url.setPlaceholderText("ws://assistant.local:8765/v1/device")
        self.name = QLineEdit(cfg["name"])
        self.name.setPlaceholderText(cfg.name())
        self.owner = QLineEdit(cfg["owner"])
        self.owner.setPlaceholderText("alice")
        self.owner.setToolTip(tr("owner_hint"))
        owner_hint = QLabel(tr("owner_hint"))
        owner_hint.setStyleSheet("color: palette(placeholder-text);")
        owner_hint.setWordWrap(True)

        self.hotkey = QKeySequenceEdit(QKeySequence(cfg["hotkey"]))
        try:
            self.hotkey.setMaximumSequenceLength(1)
            self.hotkey.setClearButtonEnabled(True)
        except AttributeError:
            pass
        self.hotkey_note = QLabel()
        self.hotkey_note.setWordWrap(True)
        self.hotkey_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if is_wayland():
            self.hotkey_note.setText(tr("wayland_hotkey", activate_command()))
        elif hotkey_error:
            self.hotkey_note.setText(tr("hotkey_unavailable", hotkey_error))
        self.hotkey_note.setVisible(bool(self.hotkey_note.text()))

        self.autostart = QCheckBox(tr("autostart"))
        self.autostart.setChecked(autostart.is_enabled())
        self.speak = QCheckBox(tr("speak_typed"))
        self.speak.setChecked(bool(cfg["speak_typed"]))
        self.language = QComboBox()
        for code, label in (("auto", tr("lang_auto")), ("fr", "Français"), ("en", "English")):
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(cfg["language"])))

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dev = QLabel(cfg["device_id"])
        dev.setTextInteractionFlags(Qt.TextSelectableByMouse)
        copy = QPushButton("⧉")
        copy.setFixedWidth(32)
        copy.setToolTip("Copy")
        copy.clicked.connect(lambda: self._copy(cfg["device_id"]))
        devrow = QHBoxLayout()
        devrow.addWidget(dev, 1)
        devrow.addWidget(copy)

        for label in (owner_hint, self.hotkey_note, self.status):
            label.setMinimumWidth(400)      # wrapped labels get squeezed in a form otherwise
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow(tr("server_url"), self.url)
        form.addRow(tr("device_name"), self.name)
        form.addRow(tr("owner"), self.owner)
        form.addRow("", owner_hint)
        keyrow = QHBoxLayout()
        keyrow.addWidget(self.hotkey, 1)
        menu_key = QPushButton(tr("menu_key"))
        menu_key.setToolTip(tr("menu_key_tip"))
        # pressing Menu in the capture field opens a context menu instead
        menu_key.clicked.connect(lambda: self.hotkey.setKeySequence(QKeySequence(Qt.Key_Menu)))
        keyrow.addWidget(menu_key)
        form.addRow(tr("hotkey"), keyrow)
        form.addRow("", self.hotkey_note)
        form.addRow("", self.autostart)
        form.addRow("", self.speak)
        form.addRow(tr("language"), self.language)
        form.addRow(tr("status"), self.status)
        form.addRow(tr("device_id"), devrow)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr("save"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

        assistant.status_changed.connect(self.show_status)
        self.show_status(assistant.status, assistant.status_detail)

    def _copy(self, text):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)

    def show_status(self, status, detail=""):
        text = tr(status) if status in ("online", "pending", "connecting", "offline", "error") else status
        if status == "online" and detail:
            text += " — %s" % detail
        elif status in ("offline", "error") and detail:
            text += " — %s" % detail
        self.status.setText('<span style="color:%s">●</span> %s' % (STATUS_COLORS.get(status, "#8E8E93"),
                                                                    text.replace("<", "&lt;")))

    def changes(self):
        seq = self.hotkey.keySequence().toString(QKeySequence.PortableText)
        return {"server_url": self.url.text().strip() or self.cfg["server_url"],
                "name": self.name.text().strip(),
                "owner": self.owner.text().strip().lower(),
                "hotkey": seq,
                "autostart": self.autostart.isChecked(),
                "speak_typed": self.speak.isChecked(),
                "language": self.language.currentData()}
