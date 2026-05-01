"""bridge.py
UiBridge — adapter from REX runtime callbacks to Qt signals.

The runtime thread invokes ``UiBridge.callback(event, **payload)`` from its
own asyncio loop. Each call emits a Qt signal; Qt's queued-connection
delivery marshals it back to the main thread, where the tray, HUD, and
settings widgets pick it up. Signals are thread-safe to emit from any
thread, so this is the only object that crosses the boundary.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    state_changed = Signal(str, dict)         # state_name ("idle"/"listening"/"paused"/"error"), extras
    command_matched = Signal(str, str)        # action_name, text
    command_unmatched = Signal(str)           # text
    error = Signal(str)                       # message

    def callback(self, event: str, **payload: Any) -> None:
        if event.startswith("state."):
            name = event.split(".", 1)[1]
            self.state_changed.emit(name, dict(payload))
        elif event == "match":
            self.command_matched.emit(
                str(payload.get("action", "")),
                str(payload.get("text", "")),
            )
        elif event == "no_match":
            self.command_unmatched.emit(str(payload.get("text", "")))
        elif event == "error":
            self.error.emit(str(payload.get("message", "unknown error")))
