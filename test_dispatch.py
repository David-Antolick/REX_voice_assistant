"""Tests for the single dispatch seam (rex_main/matcher.dispatch_text).

Both dispatch paths — the standard VAD -> Whisper -> matcher pipeline and
the low-latency FastVAD path — funnel through dispatch_text(). These tests
pin the behaviour the two paths used to disagree about; see
docs/PHASE0_DISPATCH.md for what drifted and why.

Nothing here needs audio hardware or a loaded model: the compiled pattern
table is swapped for a fake, so the real action registry is untouched.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from rex_main import matcher
from rex_main.matcher import dispatch_command, dispatch_text


# Fixtures

class FakeGate:
    """Stand-in for wake_word.ListeningState."""

    def __init__(self, active: bool = True):
        self._active = active
        self.activate_calls = 0

    def is_active(self) -> bool:
        return self._active

    def activate(self, window_s=None) -> None:
        self.activate_calls += 1


class FakeFlag:
    """Stand-in for a threading.Event-like pause flag."""

    def __init__(self, value: bool = False):
        self._value = value

    def is_set(self) -> bool:
        return self._value


class Recorder:
    """Collects handler invocations and ui_callback events."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.events: list[tuple[str, dict]] = []

    def callback(self, event: str, **payload):
        self.events.append((event, payload))

    def events_named(self, name: str) -> list[dict]:
        return [payload for ev, payload in self.events if ev == name]


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


@pytest.fixture
def table(monkeypatch, rec):
    """Swap the compiled dispatch table for a small predictable one.

    Returns the Recorder so tests can assert on handler calls. 'search X'
    stands in for a real no_early_match action (variable trailing args).
    """
    def plain(*args):
        rec.calls.append(("plain", args))

    def with_args(*args):
        rec.calls.append(("with_args", args))

    def boom(*args):
        rec.calls.append(("boom", args))
        raise RuntimeError("handler exploded")

    fake_table = [
        (re.compile(r"^skip song$", re.I), "t_plain", plain),
        (re.compile(r"^search (.+)$", re.I), "t_search", with_args),
        (re.compile(r"^detonate$", re.I), "t_boom", boom),
    ]
    monkeypatch.setattr(matcher, "_DISPATCH_TABLE", fake_table)
    monkeypatch.setattr(matcher, "NO_EARLY_MATCH_COMMANDS", {"t_search"})
    return rec


# Matching and execution

def test_match_runs_handler(table):
    result = dispatch_text("skip song")
    assert result.matched and result.executed
    assert result.action == "t_plain"
    assert table.calls == [("plain", ())]


def test_match_passes_captured_args(table):
    result = dispatch_text("search bohemian rhapsody")
    assert result.executed
    assert table.calls == [("with_args", ("bohemian rhapsody",))]


def test_leading_and_trailing_whitespace_is_ignored(table):
    assert dispatch_text("  skip song  ").executed


def test_match_event_carries_the_recognized_text(table):
    """Regression guard: the FastVAD path used to emit text="" on match."""
    dispatch_text("skip song", ui_callback=table.callback)
    matches = table.events_named("match")
    assert len(matches) == 1
    assert matches[0]["text"] == "skip song"
    assert matches[0]["action"] == "t_plain"


def test_exec_ms_reported_on_success(table):
    assert dispatch_text("skip song").exec_ms is not None


# No-match feedback — the early/final asymmetry

def test_no_match_is_reported_on_a_final_pass(table):
    """Regression guard: this event was unreachable on the default path."""
    result = dispatch_text("what is the airspeed velocity", ui_callback=table.callback)
    assert not result.matched
    misses = table.events_named("no_match")
    assert len(misses) == 1
    assert misses[0]["text"] == "what is the airspeed velocity"


def test_no_match_is_silent_on_an_early_pass(table):
    """FastVAD re-transcribes mid-utterance; a miss per partial would
    strobe the HUD's 'didn't catch that' through every sentence."""
    result = dispatch_text("what is the airsp", ui_callback=table.callback, early=True)
    assert not result.matched
    assert table.events_named("no_match") == []


# no_early_match deferral

def test_no_early_match_action_defers_on_an_early_pass(table):
    result = dispatch_text("search bohemian", early=True)
    assert result.matched and result.deferred
    assert not result.executed
    assert table.calls == []


def test_no_early_match_action_runs_on_a_final_pass(table):
    result = dispatch_text("search bohemian rhapsody", early=False)
    assert result.executed and not result.deferred
    assert table.calls == [("with_args", ("bohemian rhapsody",))]


def test_early_pass_still_runs_ordinary_actions(table):
    result = dispatch_text("skip song", early=True)
    assert result.executed
    assert table.calls == [("plain", ())]


# Wake gate

def test_closed_gate_suppresses_execution(table, monkeypatch):
    suppressed: list[str] = []
    monkeypatch.setattr(
        matcher.metrics, "record_command_suppressed", suppressed.append
    )

    result = dispatch_text("skip song", listening_state=FakeGate(active=False))
    assert result.matched and result.suppressed
    assert not result.executed
    assert table.calls == []
    assert suppressed == ["t_plain"]


def test_open_gate_refreshes_the_listening_window(table):
    """Multi-step interactions rely on each match extending the window."""
    gate = FakeGate(active=True)
    assert dispatch_text("skip song", listening_state=gate).executed
    assert gate.activate_calls == 1


def test_deferred_action_does_not_burn_a_suppression(table, monkeypatch):
    suppressed: list[str] = []
    monkeypatch.setattr(
        matcher.metrics, "record_command_suppressed", suppressed.append
    )
    dispatch_text("search bohemian", listening_state=FakeGate(active=False), early=True)
    assert suppressed == []


# Paused

def test_paused_drops_everything(table):
    result = dispatch_text(
        "skip song", ui_callback=table.callback, paused=FakeFlag(True)
    )
    assert not result.matched
    assert table.calls == []
    assert table.events == []


# Error boundary

def test_throwing_handler_does_not_propagate(table):
    """Regression guard: FastVAD has no try/except of its own, so an
    escaping exception would kill the task and take the assistant down."""
    result = dispatch_text("detonate")
    assert result.matched and result.executed
    assert result.exec_ms is None
    assert table.calls == [("boom", ())]


def test_dispatch_still_works_after_a_handler_throws(table):
    dispatch_text("detonate")
    assert dispatch_text("skip song").executed


def test_ui_callback_exception_does_not_propagate(table):
    def bad_callback(event, **payload):
        raise RuntimeError("ui exploded")

    assert dispatch_text("skip song", ui_callback=bad_callback).executed


# Standard-mode queue loop
#
# dispatch_command is the other caller of the seam. These drive its real
# loop so the standard path can't regress unnoticed while FastVAD gets
# all the attention.

def _drain(texts, **kwargs):
    """Feed texts through dispatch_command and stop once consumed."""
    async def go():
        q: asyncio.Queue[str] = asyncio.Queue()
        for t in texts:
            q.put_nowait(t)
        task = asyncio.create_task(dispatch_command(q, **kwargs))
        await q.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())


def test_standard_loop_runs_handlers(table):
    _drain(["skip song", "search bohemian rhapsody"])
    assert table.calls == [
        ("plain", ()),
        ("with_args", ("bohemian rhapsody",)),
    ]


def test_standard_loop_reports_no_match(table):
    """Standard mode always dispatches a final pass, never an early one."""
    _drain(["complete nonsense"], ui_callback=table.callback)
    assert len(table.events_named("no_match")) == 1


def test_standard_loop_survives_a_throwing_handler(table):
    _drain(["detonate", "skip song"])
    assert table.calls == [("boom", ()), ("plain", ())]
