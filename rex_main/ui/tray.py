"""tray.py
RexTray — system tray icon with state-driven glyph and right-click menu.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
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


_STARTUP_SHORTCUT_NAME = "Rex Voice Assistant.lnk"


def _startup_dir() -> Optional[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_shortcut_path() -> Optional[Path]:
    d = _startup_dir()
    return d / _STARTUP_SHORTCUT_NAME if d else None


def _find_rex_gui_exe() -> Optional[Path]:
    """Locate rex-gui.exe so the shortcut points at the windowless launcher."""
    found = shutil.which("rex-gui.exe") or shutil.which("rex-gui")
    if found:
        return Path(found)
    py_dir = Path(sys.executable).parent
    for cand in (py_dir / "rex-gui.exe", py_dir / "Scripts" / "rex-gui.exe"):
        if cand.exists():
            return cand
    return None


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def startup_shortcut_exists() -> bool:
    p = _startup_shortcut_path()
    return bool(p and p.exists())


def enable_startup_shortcut() -> Path:
    """Create the Startup-folder shortcut. Returns the .lnk path on success."""
    if sys.platform != "win32":
        raise RuntimeError("Startup shortcut is only supported on Windows.")
    lnk = _startup_shortcut_path()
    if lnk is None:
        raise RuntimeError("Could not locate the user Startup folder (APPDATA missing).")
    target = _find_rex_gui_exe()
    if target is None:
        raise RuntimeError(
            "rex-gui.exe not found on PATH. Reinstall with `pip install -e .` "
            "to register the windowless launcher."
        )
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(lnk)) + "); "
        "$s.TargetPath = " + _ps_quote(str(target)) + "; "
        "$s.WorkingDirectory = " + _ps_quote(str(target.parent)) + "; "
        "$s.WindowStyle = 7; "
        "$s.Description = 'Rex Voice Assistant (tray)'; "
        "$s.Save()"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=True,
        creationflags=creationflags,
        capture_output=True,
    )
    return lnk


def disable_startup_shortcut() -> None:
    lnk = _startup_shortcut_path()
    if lnk and lnk.exists():
        lnk.unlink()


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

        self._action_startup = QAction("Launch at Windows startup", self._menu)
        self._action_startup.setCheckable(True)
        self._action_startup.setToolTip(
            "Adds a shortcut to your Windows Startup folder. "
            "You can also toggle this in Settings → Apps → Startup."
        )
        self._action_startup.triggered.connect(self._on_toggle_startup)
        if sys.platform != "win32":
            self._action_startup.setEnabled(False)
        self._menu.addAction(self._action_startup)

        action_about = QAction("About Rex", self._menu)
        action_about.triggered.connect(self._show_about)
        self._menu.addAction(action_about)

        self._menu.addSeparator()
        action_quit = QAction("Quit", self._menu)
        action_quit.triggered.connect(self._on_quit)
        self._menu.addAction(action_quit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        # Refresh on menu open so Settings → Startup toggles or manual file edits
        # stay reflected in the checkmark.
        self._menu.aboutToShow.connect(self._refresh_startup_check)

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

    def _refresh_startup_check(self) -> None:
        if sys.platform != "win32":
            return
        self._action_startup.blockSignals(True)
        try:
            self._action_startup.setChecked(startup_shortcut_exists())
        finally:
            self._action_startup.blockSignals(False)

    def _on_toggle_startup(self, checked: bool) -> None:
        try:
            if checked:
                lnk = enable_startup_shortcut()
                logger.info("Startup shortcut created at %s", lnk)
            else:
                disable_startup_shortcut()
                logger.info("Startup shortcut removed")
        except Exception as exc:
            logger.exception("startup shortcut toggle failed")
            QMessageBox.warning(
                None,
                "Rex — Startup shortcut",
                f"Could not {'create' if checked else 'remove'} the Startup shortcut:\n\n{exc}",
            )
            self._refresh_startup_check()

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
