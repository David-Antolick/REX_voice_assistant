"""tray.py
RexTray — system tray icon with state-driven glyph and right-click menu.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from rex_main.ui.bridge import UiBridge
from rex_main.ui.icons import make_icon

logger = logging.getLogger(__name__)


_STATE_TOOLTIPS = {
    "idle": "Rex — ready",
    "listening": "Rex — listening",
    "processing": "Rex — thinking",
    "paused": "Rex — paused",
    "error": "Rex — error",
}


class RexTray(QObject):
    def __init__(
        self,
        bridge: UiBridge,
        paused_event: threading.Event,
        config: dict,
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_restart: Callable[[], None],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._paused = paused_event
        self._config = config
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._on_restart = on_restart

        self._tray = QSystemTrayIcon(make_icon("idle"))
        self._tray.setToolTip(_STATE_TOOLTIPS["idle"])

        self._menu = QMenu()
        self._action_pause = QAction("Pause Listening", self._menu)
        self._action_pause.triggered.connect(self._toggle_pause)
        self._menu.addAction(self._action_pause)
        self._menu.addSeparator()

        action_settings = QAction("Settings…", self._menu)
        action_settings.triggered.connect(self._on_open_settings)
        self._menu.addAction(action_settings)

        action_restart = QAction("Restart Rex", self._menu)
        action_restart.setToolTip("Stop and re-launch the assistant runtime (keeps the tray icon).")
        action_restart.triggered.connect(self._on_restart)
        self._menu.addAction(action_restart)

        action_logs = QAction("Open logs folder", self._menu)
        action_logs.triggered.connect(self._open_logs)
        self._menu.addAction(action_logs)

        action_about = QAction("About Rex", self._menu)
        action_about.triggered.connect(self._show_about)
        self._menu.addAction(action_about)

        self._menu.addSeparator()
        action_quit = QAction("Quit", self._menu)
        action_quit.triggered.connect(self._on_quit)
        self._menu.addAction(action_quit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)

        # Auto-return to idle after the listening window closes, since the
        # runtime emits state.idle only when something explicitly calls
        # listening_state.deactivate(). Refreshed on each new listening event.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(lambda: self._set_state("idle"))

        bridge.state_changed.connect(self._on_state_changed)
        bridge.command_matched.connect(lambda *_: self._set_state("idle"))
        bridge.command_unmatched.connect(lambda *_: self._set_state("idle"))

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    # State handling

    def _on_state_changed(self, name: str, payload: dict) -> None:
        self._set_state(name)
        if name == "listening":
            window_s = float(payload.get("window_s") or 6.0)
            self._idle_timer.start(int(window_s * 1000))

    def _set_state(self, name: str) -> None:
        # Don't override paused glyph based on runtime emissions.
        if self._paused.is_set() and name != "paused":
            return
        self._tray.setIcon(make_icon(name))
        self._tray.setToolTip(_STATE_TOOLTIPS.get(name, "REX"))

    # Menu actions

    def _toggle_pause(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
            self._action_pause.setText("Pause Listening")
            self._set_state("idle")
        else:
            self._paused.set()
            self._action_pause.setText("Resume Listening")
            self._idle_timer.stop()
            self._tray.setIcon(make_icon("paused"))
            self._tray.setToolTip(_STATE_TOOLTIPS["paused"])

    def _open_logs(self) -> None:
        log_path_str = self._config.get("logging", {}).get("file", "~/.rex/logs/rex.log")
        log_dir = Path(os.path.expanduser(log_path_str)).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def _show_about(self) -> None:
        try:
            from importlib.metadata import version

            v = version("rex-voice-assistant")
        except Exception:
            v = "unknown"
        QMessageBox.about(
            None,
            "About Rex",
            f"Rex Voice Assistant\nVersion {v}\n\nOffline, local-first voice control.",
        )

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._on_open_settings()
