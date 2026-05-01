"""hud.py
RecognitionHud — a small frameless always-on-top floater that flashes
the result of each voice command. Click-through on Windows so it can't
eat clicks during gameplay.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

logger = logging.getLogger(__name__)

_MATCH_DURATION_MS = 1500
_NO_MATCH_DURATION_MS = 800
_MARGIN_PX = 40


class RecognitionHud(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._label = QLabel("", self)
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        self._label.setFont(font)
        self._label.setStyleSheet("color: white; padding: 12px 18px;")
        self._label.setAlignment(Qt.AlignCenter)

        self._bg_color = QColor(30, 30, 30, 220)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self._clickthrough_applied = False

    # Public slots — connect bridge signals to these.

    def flash_match(self, action: str, _text: str) -> None:
        pretty = action.replace("_", " ")
        self._show_text(f"✓ {pretty}", QColor(39, 174, 96, 230), _MATCH_DURATION_MS)

    def flash_no_match(self, _text: str) -> None:
        self._show_text("didn’t catch that", QColor(60, 60, 60, 220), _NO_MATCH_DURATION_MS)

    # Internals

    def _show_text(self, text: str, bg_color: QColor, duration_ms: int) -> None:
        self._bg_color = bg_color
        self._label.setText(text)
        self._label.adjustSize()

        margin_h, margin_v = 18, 12
        w = self._label.width() + margin_h * 2
        h = self._label.height() + margin_v * 2
        self.resize(w, h)
        self._label.move(margin_h, margin_v)

        self._reposition()
        self.show()

        if not self._clickthrough_applied and sys.platform == "win32":
            try:
                _set_clickthrough(int(self.winId()))
                self._clickthrough_applied = True
            except Exception:
                logger.debug("click-through setup failed; HUD will still work", exc_info=True)

        self._timer.start(duration_ms)

    def _reposition(self) -> None:
        cursor_pos: QPoint = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo: QRect = screen.availableGeometry()
        x = geo.right() - self.width() - _MARGIN_PX
        y = geo.bottom() - self.height() - _MARGIN_PX
        self.move(x, y)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
        painter.setBrush(self._bg_color)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)


def _set_clickthrough(hwnd: int) -> None:
    """Add WS_EX_TRANSPARENT + WS_EX_LAYERED so clicks pass through."""
    import ctypes
    from ctypes import wintypes

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000

    user32 = ctypes.windll.user32
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long

    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
