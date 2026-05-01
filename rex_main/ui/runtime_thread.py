"""runtime_thread.py
AssistantThread — runs the asyncio voice-assistant loop on a worker thread.

The Qt main thread owns the UI; this thread owns its own asyncio event
loop and runs ``run_assistant``. Stop is requested by scheduling
``loop.stop()`` thread-safely; the run() method handles a clean unwind.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

from rex_main.rex import run_assistant

logger = logging.getLogger(__name__)


class AssistantThread(QThread):
    finished_with_error = Signal(str)

    def __init__(
        self,
        opts: Any,
        config: dict,
        ui_callback: Callable[..., None],
        paused_event: threading.Event,
        parent: Optional[QThread] = None,
    ) -> None:
        super().__init__(parent)
        self._opts = opts
        self._config = config
        self._ui_callback = ui_callback
        self._paused = paused_event
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_task: Optional[asyncio.Task] = None

    def run(self) -> None:  # called on the worker thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._main_task = self._loop.create_task(
                run_assistant(
                    self._opts,
                    self._config,
                    ui_callback=self._ui_callback,
                    paused=self._paused,
                )
            )
            self._loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            logger.info("assistant task cancelled")
        except Exception as exc:  # noqa: BLE001
            logger.exception("assistant thread crashed")
            self.finished_with_error.emit(str(exc))
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()
            self._loop = None
            self._main_task = None

    def request_stop(self) -> None:
        loop = self._loop
        task = self._main_task
        if loop is None or not loop.is_running():
            return

        def _cancel() -> None:
            if task is not None and not task.done():
                task.cancel()

        loop.call_soon_threadsafe(_cancel)
