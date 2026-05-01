"""app.py
launch_tray_app — entry point for the REX desktop UI.

Owns the QApplication, builds the tray + HUD + settings widgets, and
spawns the assistant runtime on a worker thread. Returns the Qt exit
code so the CLI can pass it back to the OS.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from rex_main.ui.bridge import UiBridge
from rex_main.ui.hud import RecognitionHud
from rex_main.ui.icons import make_app_icon
from rex_main.ui.runtime_thread import AssistantThread
from rex_main.ui.settings import SettingsDialog
from rex_main.ui.tray import RexTray

logger = logging.getLogger(__name__)


def launch_tray_app(opts: Any, config: dict) -> int:
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("REX")
    app.setOrganizationName("REX")
    app.setWindowIcon(make_app_icon())
    app.setQuitOnLastWindowClosed(False)

    if not RexTray.is_available():
        logger.warning("System tray not available — falling back to console mode.")
        print("System tray not available on this system; running in console mode.", flush=True)
        from rex_main.rex import run_assistant
        import asyncio

        asyncio.run(run_assistant(opts, config))
        return 0

    print("Rex tray icon ready. Right-click it for Settings, Pause, Restart, or Quit.", flush=True)

    paused_event = threading.Event()
    bridge = UiBridge()

    hud = RecognitionHud()
    bridge.command_matched.connect(hud.flash_match)
    bridge.command_unmatched.connect(hud.flash_no_match)

    # The runtime thread is replaceable so settings can restart it in place
    # without taking the tray icon down. Holder dict gets us a writable cell
    # captured by the closures below.
    runtime_holder: dict[str, AssistantThread] = {}

    def _on_runtime_error(msg: str) -> None:
        logger.error("runtime error: %s", msg)
        QMessageBox.critical(
            None,
            "Rex runtime error",
            f"The voice assistant stopped:\n\n{msg}\n\nUse the tray Restart or Quit menu to recover.",
        )

    def _build_runtime(opts_for_run: Any, config_for_run: dict) -> AssistantThread:
        rt = AssistantThread(opts_for_run, config_for_run, bridge.callback, paused_event)
        rt.finished_with_error.connect(_on_runtime_error)
        return rt

    runtime_holder["current"] = _build_runtime(opts, config)

    settings_dialog: dict[str, SettingsDialog | None] = {"current": None}

    def restart_runtime() -> bool:
        """Stop the current assistant thread and start a fresh one with the
        live config. Returns True if the new thread started cleanly."""
        old = runtime_holder.get("current")
        if old is not None:
            old.request_stop()
            if not old.wait(7000):
                logger.warning("runtime thread did not exit within 7s; terminating")
                old.terminate()
                old.wait(2000)
        bridge.state_changed.emit("idle", {})
        new = _build_runtime(opts, config)
        runtime_holder["current"] = new
        new.start()
        return True

    def open_settings() -> None:
        if settings_dialog["current"] is not None:
            settings_dialog["current"].raise_()
            settings_dialog["current"].activateWindow()
            return
        dlg = SettingsDialog(config, on_save=_handle_save)
        settings_dialog["current"] = dlg
        try:
            dlg.exec()
        finally:
            settings_dialog["current"] = None

    def _handle_save(new_config: dict, restart_needed: bool) -> None:
        # Music-only changes apply live by re-configuring backends.
        try:
            from rex_main.actions.service import configure_from_config

            configure_from_config(new_config)
        except Exception:
            logger.exception("hot-reload of services failed")
        # Mutate the live config dict in place so the tray's "Open logs"
        # action reflects the latest log path without a restart.
        config.clear()
        config.update(new_config)

        if restart_needed:
            answer = QMessageBox.question(
                None,
                "Restart Rex?",
                "Some changes need Rex to restart to take effect.\n\n"
                "Restart now? (The tray icon stays.)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                restart_runtime()

    def quit_app() -> None:
        rt = runtime_holder.get("current")
        if rt is not None:
            rt.request_stop()
            if not rt.wait(5000):
                logger.warning("runtime thread did not exit within 5s; terminating")
                rt.terminate()
        app.quit()

    tray = RexTray(
        bridge,
        paused_event,
        config,
        on_open_settings=open_settings,
        on_quit=quit_app,
        on_restart=restart_runtime,
    )

    tray.show()
    runtime_holder["current"].start()

    rc = app.exec()
    rt = runtime_holder.get("current")
    if rt is not None:
        rt.request_stop()
        rt.wait(5000)
    return rc
