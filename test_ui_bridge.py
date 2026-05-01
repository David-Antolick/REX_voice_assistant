"""Smoke tests for the UI bridge.

Verifies that the runtime → Qt-signal adapter dispatches each event type
to the correct signal with the correct payload. Uses QSignalSpy so the
test does not depend on any real UI being shown.

Skipped if PySide6 is not importable, so the existing CI matrix doesn't
have to install Qt.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402

from rex_main.ui.bridge import UiBridge  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_state_event_emits_state_changed(qapp):
    bridge = UiBridge()
    spy = QSignalSpy(bridge.state_changed)
    bridge.callback("state.listening", window_s=6.0)
    assert spy.count() == 1
    args = spy.at(0)
    assert args[0] == "listening"
    assert args[1] == {"window_s": 6.0}


def test_match_event_emits_command_matched(qapp):
    bridge = UiBridge()
    spy = QSignalSpy(bridge.command_matched)
    bridge.callback("match", action="skip_song", text="skip song", args=())
    assert spy.count() == 1
    args = spy.at(0)
    assert args[0] == "skip_song"
    assert args[1] == "skip song"


def test_no_match_event_emits_command_unmatched(qapp):
    bridge = UiBridge()
    spy = QSignalSpy(bridge.command_unmatched)
    bridge.callback("no_match", text="banana phone")
    assert spy.count() == 1
    assert spy.at(0)[0] == "banana phone"


def test_unknown_event_is_ignored(qapp):
    bridge = UiBridge()
    state_spy = QSignalSpy(bridge.state_changed)
    match_spy = QSignalSpy(bridge.command_matched)
    bridge.callback("totally.bogus", whatever=1)
    assert state_spy.count() == 0
    assert match_spy.count() == 0
