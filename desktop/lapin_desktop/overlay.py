"""The overlay: a small frameless card at the bottom center of the screen with
an animated orb, the state, what was heard, the answer, Yes/No buttons for
confirmations and a field to type a request."""

import math
import time

from PySide6.QtCore import (QEasingCurve, QEvent, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation,
                            QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QConicalGradient, QCursor, QFont, QGuiApplication, QKeySequence, QPainter,
                           QPainterPath, QPen, QRadialGradient, QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from .i18n import confirm_text, tr

SHADOW = 22             # transparent margin around the card for its shadow
CARD_WIDTH = 580
RADIUS = 24
REPLY_MAX_HEIGHT = 260
ORB_A = QColor(124, 92, 255)     # violet
ORB_B = QColor(46, 197, 255)     # cyan
ORB_C = QColor(255, 94, 170)     # pink, while speaking


def is_dark():
    hints = QGuiApplication.styleHints()
    try:
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:
        pass
    return QGuiApplication.palette().window().color().lightness() < 128


def theme(dark):
    if dark:
        return {"card": QColor(28, 28, 34, 238), "border": QColor(255, 255, 255, 30), "text": "#F2F2F5",
                "muted": "#A3A3AE", "field": "rgba(255,255,255,0.08)", "field_border": "rgba(255,255,255,0.12)",
                "button": "rgba(255,255,255,0.10)", "button_hover": "rgba(255,255,255,0.18)", "shadow": 110,
                "accent": "#8B74FF"}
    return {"card": QColor(252, 252, 254, 242), "border": QColor(0, 0, 0, 26), "text": "#1C1C22",
            "muted": "#6B6B76", "field": "rgba(0,0,0,0.05)", "field_border": "rgba(0,0,0,0.10)",
            "button": "rgba(0,0,0,0.06)", "button_hover": "rgba(0,0,0,0.11)", "shadow": 60,
            "accent": "#6A4DFF"}


class Orb(QWidget):
    """Animated indicator: ripples following the microphone level while
    listening, a spinning gradient while thinking, a pulse while speaking."""

    def __init__(self, level_fn=None, out_level_fn=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(64, 64)
        self.mode = "idle"
        self.level_fn = level_fn or (lambda: 0.0)
        self.out_level_fn = out_level_fn or (lambda: 0.0)
        self.level = 0.0
        self.phase = 0.0
        self.t0 = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def showEvent(self, e):
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()

    def _tick(self):
        if self.mode == "listening":
            target = self.level_fn()
        elif self.mode == "speaking":
            target = self.out_level_fn()
        else:
            target = 0.0
        self.level += (target - self.level) * (0.45 if target > self.level else 0.12)
        speed = {"thinking": 0.16, "listening": 0.05, "speaking": 0.07}.get(self.mode, 0.02)
        self.phase = (self.phase + speed) % (2 * math.pi)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        t = time.monotonic() - self.t0
        base = 15.0
        if self.mode in ("done", "error"):
            color = QColor(52, 199, 89) if self.mode == "done" else QColor(255, 69, 58)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, base + 3, base + 3)
            pen = QPen(QColor("white"), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            if self.mode == "done":
                path = QPainterPath(QPointF(c.x() - 7, c.y() + 0.5))
                path.lineTo(c.x() - 2, c.y() + 5.5)
                path.lineTo(c.x() + 7.5, c.y() - 5)
                p.drawPath(path)
            else:
                p.drawLine(QPointF(c.x(), c.y() - 7), QPointF(c.x(), c.y() + 2))
                p.drawPoint(QPointF(c.x(), c.y() + 7))
            return

        if self.mode == "listening":
            # ripples: three rings going out, bigger with the voice
            for i in range(3):
                k = ((t * 0.9 + i / 3) % 1.0)
                r = min(30.0, base + 4 + k * (5 + 8 * self.level))
                a = int(120 * (1 - k) * (0.35 + self.level))
                col = QColor(ORB_B)
                col.setAlpha(max(0, min(255, a)))
                p.setPen(QPen(col, 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r, r)
            radius = base + 1 + 6 * self.level
        elif self.mode == "speaking":
            radius = base + 1 + 6 * self.level + 1.2 * math.sin(t * 5)
        elif self.mode == "thinking":
            radius = base - 1 + 1.2 * math.sin(t * 3)
        else:
            radius = base - 2 + 0.8 * math.sin(t * 1.6)

        # soft glow, fading out before the widget's edge
        gr = min(radius * 1.9, self.width() / 2 - 1)
        glow = QRadialGradient(c, gr)
        gcol = QColor(ORB_C if self.mode == "speaking" else ORB_A)
        gcol.setAlpha(90 if self.mode != "idle" else 40)
        glow.setColorAt(0.45, gcol)
        gcol.setAlpha(0)
        glow.setColorAt(1.0, gcol)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, gr, gr)

        # the orb: a rotating conical gradient
        cg = QConicalGradient(c, math.degrees(self.phase))
        second = ORB_C if self.mode == "speaking" else ORB_B
        cg.setColorAt(0.0, ORB_A)
        cg.setColorAt(0.5, second)
        cg.setColorAt(1.0, ORB_A)
        p.setBrush(cg)
        p.drawEllipse(c, radius, radius)
        # highlight
        hl = QRadialGradient(QPointF(c.x() - radius * 0.35, c.y() - radius * 0.4), radius)
        hl.setColorAt(0, QColor(255, 255, 255, 150))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(hl)
        p.drawEllipse(c, radius, radius)

        if self.mode == "thinking":
            pen = QPen(QColor(255, 255, 255, 220), 2.4, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            r = radius + 5
            start = int(-math.degrees(self.phase * 2) * 16)
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), start, 100 * 16)


class MicButton(QAbstractButton):
    """Round button with a microphone glyph; filled while listening."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.active = False
        self.colors = theme(True)

    def sizeHint(self):
        return QSize(36, 36)

    def set_active(self, on):
        self.active = on
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(Qt.NoPen)
        if self.active:
            p.setBrush(QColor(self.colors["accent"]))
            p.drawEllipse(r)
        if self.underMouse() and not self.active:
            hover = QColor(self.colors["text"])
            hover.setAlpha(30)
            p.setBrush(hover)
            p.drawEllipse(r)
        col = QColor("white") if self.active else QColor(self.colors["text"])
        cx, cy = self.width() / 2, self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(cx - 4, cy - 10, 8, 13), 4, 4)
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - 7, cy - 8, 14, 14), 200 * 16, 140 * 16)
        p.drawLine(QPointF(cx, cy + 6), QPointF(cx, cy + 9.5))


class Overlay(QWidget):
    dismissed = Signal()            # Esc, click elsewhere, close button
    text_submitted = Signal(str)
    mic_clicked = Signal()
    confirm_answered = Signal(bool)
    typing_started = Signal()

    def __init__(self, level_fn=None, out_level_fn=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Lapin")
        self.setFixedWidth(CARD_WIDTH + 2 * SHADOW)
        self.state = "idle"
        self.reply_text = ""
        self.last_tool = lambda: 0.0
        self.shown_at = 0.0
        self.hiding = False
        self.dark = is_dark()
        self.colors = theme(self.dark)
        self._bottom = 0

        self.orb = Orb(level_fn, out_level_fn)
        self.state_label = QLabel(tr("idle"))
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 1.18)
        f.setWeight(QFont.DemiBold)
        self.state_label.setFont(f)
        self.hint_label = QLabel(tr("hint"))
        self.hint_label.setObjectName("muted")
        self.close_btn = QToolButton()
        self.close_btn.setText("✕")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setAutoRaise(True)
        self.close_btn.clicked.connect(self.dismissed)

        head_text = QVBoxLayout()
        head_text.setSpacing(0)
        head_text.addStretch(1)
        head_text.addWidget(self.state_label)
        head_text.addWidget(self.hint_label)
        head_text.addStretch(1)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.orb)
        head.addLayout(head_text, 1)
        head.addWidget(self.close_btn, 0, Qt.AlignTop)

        self.heard = QLabel()
        self.heard.setObjectName("muted")
        self.heard.setWordWrap(True)
        self.heard.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.heard.hide()

        self.reply = QTextBrowser()
        self.reply.setFrameShape(QFrame.NoFrame)
        self.reply.setOpenExternalLinks(True)
        self.reply.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.reply.document().setDocumentMargin(0)
        self.reply.viewport().setAutoFillBackground(False)
        rf = QFont(self.font())
        rf.setPointSizeF(rf.pointSizeF() * 1.12)
        self.reply.setFont(rf)
        self.reply.hide()

        self.confirm_box = QFrame()
        self.confirm_box.setObjectName("confirm")
        cl = QHBoxLayout(self.confirm_box)
        cl.setContentsMargins(14, 10, 10, 10)
        self.confirm_label = QLabel()
        self.confirm_label.setWordWrap(True)
        self.no_btn = QPushButton(tr("no"))
        self.yes_btn = QPushButton(tr("yes"))
        self.yes_btn.setObjectName("primary")
        for b in (self.no_btn, self.yes_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(78)
        self.no_btn.clicked.connect(lambda: self._answer(False))
        self.yes_btn.clicked.connect(lambda: self._answer(True))
        cl.addWidget(self.confirm_label, 1)
        cl.addWidget(self.no_btn)
        cl.addWidget(self.yes_btn)
        self.confirm_box.hide()

        self.field = QLineEdit()
        self.field.setPlaceholderText(tr("type_here"))
        self.field.setClearButtonEnabled(False)
        self.field.returnPressed.connect(self._submit)
        self.field.textEdited.connect(self._edited)
        self.mic = MicButton()
        self.mic.clicked.connect(self.mic_clicked)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.field, 1)
        bar.addWidget(self.mic)

        card = QVBoxLayout(self)
        card.setContentsMargins(SHADOW + 20, SHADOW + 16, SHADOW + 20, SHADOW + 16)
        card.setSpacing(10)
        card.addLayout(head)
        card.addWidget(self.heard)
        card.addWidget(self.reply)
        card.addWidget(self.confirm_box)
        card.addLayout(bar)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.dismissed.emit)

        self.anim = None
        self.autohide = QTimer(self)
        self.autohide.setSingleShot(True)
        self.autohide.timeout.connect(self._autohide)
        self.apply_theme()
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.apply_theme())
        except AttributeError:
            pass

    # ------------------------------------------------------------ look
    def apply_theme(self, dark=None):
        self.dark = is_dark() if dark is None else dark
        c = self.colors = theme(self.dark)
        self.mic.colors = c
        self.setStyleSheet("""
            QLabel { color: %(text)s; background: transparent; }
            QLabel#muted { color: %(muted)s; }
            QToolButton { color: %(muted)s; border: none; font-size: 15px; padding: 2px 6px; border-radius: 8px; }
            QToolButton:hover { background: %(button)s; }
            QLineEdit { color: %(text)s; background: %(field)s; border: 1px solid %(field_border)s;
                        border-radius: 18px; padding: 8px 14px; selection-background-color: %(accent)s; }
            QLineEdit:focus { border: 1px solid %(accent)s; }
            QPushButton { color: %(text)s; background: %(button)s; border: none; border-radius: 14px;
                          padding: 7px 16px; font-weight: 600; }
            QPushButton:hover { background: %(button_hover)s; }
            QPushButton#primary { color: white; background: %(accent)s; }
            QPushButton#primary:hover { background: %(accent)s; border: 1px solid rgba(255,255,255,0.35); }
            QFrame#confirm { background: %(field)s; border-radius: 16px; }
            QTextBrowser { color: %(text)s; background: transparent; border: none;
                           selection-background-color: %(accent)s; }
            QScrollBar:vertical { width: 6px; background: transparent; }
            QScrollBar::handle:vertical { background: %(field_border)s; border-radius: 3px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
        """ % c)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        # soft shadow: stacked translucent rounded rects, a bit lower than the card
        strength = self.colors["shadow"]
        p.setPen(Qt.NoPen)
        for i in range(SHADOW, 0, -2):
            a = int(strength * (1 - i / SHADOW) ** 2 / 9)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(card.adjusted(-i, -i + 6, i, i + 6), RADIUS + i, RADIUS + i)
        p.setBrush(self.colors["card"])
        p.setPen(QPen(self.colors["border"], 1))
        p.drawRoundedRect(card, RADIUS, RADIUS)

    def retranslate(self):
        self.hint_label.setText(tr("hint"))
        self.field.setPlaceholderText(tr("type_here"))
        self.yes_btn.setText(tr("yes"))
        self.no_btn.setText(tr("no"))
        self.set_state(self.state)

    # ------------------------------------------------------------ content
    def set_state(self, state):
        self.state = state
        self.state_label.setText(tr(state))
        if state != "idle" or self.orb.mode not in ("done", "error"):
            self.orb.set_mode(state)
        self.mic.set_active(state == "listening")
        if state != "idle":
            self.autohide.stop()

    def new_turn(self, source):
        self.orb.set_mode(self.state)
        self.heard.clear()
        self.heard.hide()
        if source != "followup":
            self.set_reply("")
        self._fit()

    def set_transcript(self, text, final=False):
        text = text.strip()
        self.heard.setText("« %s »" % text if text else "")
        self.heard.setVisible(bool(text))
        self._fit()

    def set_reply(self, text, ack=""):
        self.reply_text = text
        if not text and ack:
            text = tr("done")
            self.orb.set_mode("done")
        self.reply.setPlainText(text)
        self.reply.setVisible(bool(text))
        self._fit()
        self.reply.verticalScrollBar().setValue(0)

    def set_notice(self, text, error=False):
        self.reply_text = text
        self.reply.setPlainText(text)
        self.reply.setVisible(bool(text))
        if error:
            self.orb.set_mode("error")
        self._fit()
        if self.state == "idle":
            self.schedule_autohide(2600)

    def show_confirm(self, summary):
        self.confirm_label.setText(confirm_text(summary))
        self.confirm_box.show()
        self._fit()

    def hide_confirm(self):
        self.confirm_box.hide()
        self._fit()

    def _answer(self, yes):
        self.hide_confirm()
        self.confirm_answered.emit(yes)

    def _submit(self):
        text = self.field.text().strip()
        if text:
            self.field.clear()
            self.set_transcript(text)
            self.text_submitted.emit(text)

    def _edited(self, text):
        self.autohide.stop()
        if text and self.state == "listening":
            self.typing_started.emit()

    # ------------------------------------------------------------ geometry / animation
    def _screen(self):
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _fit(self):
        if self.reply.isVisibleTo(self):
            doc = self.reply.document()
            doc.setTextWidth(CARD_WIDTH - 40)
            if doc.size().height() > REPLY_MAX_HEIGHT:
                doc.setTextWidth(CARD_WIDTH - 40 - 10)     # room for the scroll bar
            self.reply.setFixedHeight(max(1, min(REPLY_MAX_HEIGHT, math.ceil(doc.size().height()) + 2)))
        lay = self.layout()
        w = self.width()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        h = max(h, lay.totalMinimumSize().height())
        if h != self.height():
            self.setFixedHeight(h)
            if self.isVisible() and self._bottom and not (self.anim and self.anim.state() == self.anim.State.Running):
                self.move(self.x(), self._bottom - h)

    def _target_pos(self):
        g = self._screen().availableGeometry()
        x = g.x() + (g.width() - self.width()) // 2
        self._bottom = g.y() + g.height() - 28 + SHADOW
        return QPoint(x, self._bottom - self.height())

    def present(self):
        """Shows the card (fade + slide up) and takes the keyboard focus."""
        self.autohide.stop()
        self._fit()
        if self.isVisible() and not self.hiding:
            self.raise_()
            self.activateWindow()
            return
        self.hiding = False
        end = self._target_pos()
        start = end + QPoint(0, 18)
        if self.anim:
            self.anim.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.move(start)
            self.show()
        self.shown_at = time.monotonic()
        self.raise_()
        self.activateWindow()
        self.field.setFocus()
        self.anim = self._animate(self.windowOpacity(), 1.0, self.pos(), end, 230, QEasingCurve.OutCubic)
        self.anim.start()

    def dismiss_animated(self):
        if not self.isVisible() or self.hiding:
            return
        self.hiding = True
        self.autohide.stop()
        if self.anim:
            self.anim.stop()
        self.anim = self._animate(self.windowOpacity(), 0.0, self.pos(), self.pos() + QPoint(0, 14), 170,
                                  QEasingCurve.InCubic)
        self.anim.finished.connect(self._hidden)
        self.anim.start()

    def _hidden(self):
        if self.hiding:
            self.hide()
            self.hiding = False
            self.field.clear()
            self.hide_confirm()

    def _animate(self, o0, o1, p0, p1, ms, curve):
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setStartValue(o0)
        fade.setEndValue(o1)
        fade.setDuration(ms)
        fade.setEasingCurve(curve)
        slide = QPropertyAnimation(self, b"pos")
        slide.setStartValue(p0)
        slide.setEndValue(p1)
        slide.setDuration(ms)
        slide.setEasingCurve(curve)
        group.addAnimation(fade)
        group.addAnimation(slide)
        return group

    def schedule_autohide(self, ms=None):
        if ms is None:
            ms = min(16000, 3500 + 45 * len(self.reply_text or ""))
        self.autohide.start(ms)

    def _autohide(self):
        if self.state != "idle":
            return
        if self.underMouse() or self.field.text() or self.confirm_box.isVisible():
            self.autohide.start(2000)       # the user is busy with the card: later
            return
        self.dismiss_animated()

    def hide_for_grab(self, hide):
        """Screenshots: the card shouldn't be on them."""
        if hide:
            self.setWindowOpacity(0.0)
            QGuiApplication.processEvents()
            time.sleep(0.15)
            QGuiApplication.processEvents()
        elif self.isVisible() and not self.hiding:
            self.setWindowOpacity(1.0)

    # ------------------------------------------------------------ events
    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible() \
                and not self.hiding:
            # clicked elsewhere; but not when a window we just opened (open_app...) took the focus
            recent_tool = time.monotonic() - self.last_tool() < 5
            if time.monotonic() - self.shown_at > 0.5 and not recent_tool:
                self.dismissed.emit()
        super().changeEvent(e)
