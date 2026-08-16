"""Tests for the action-discovery panel's grouping and filter logic.

The widget itself needs a display, so only the pure functions are covered
here — they are the part that can silently go wrong. The registry test is
the one that matters long-term: it fails if a new backend registers an
action the panel can't render.

Skipped if PySide6 is not importable, matching test_ui_bridge.py.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from rex_main.actions.registry import ActionSpec, ArgSpec  # noqa: E402
from rex_main.ui.discovery import (  # noqa: E402
    CommandEntry,
    build_groups,
    entry_from_spec,
    group_label,
    registry_entries,
)


def _entry(name: str, backend: str = "ytmd", summary: str = "", phrases=(), active: bool = True):
    return CommandEntry(
        name=name,
        backend=backend,
        summary=summary,
        phrases=tuple(phrases),
        args=(),
        active=active,
    )


def test_groups_are_sorted_by_backend_then_name():
    entries = [
        _entry("zulu", backend="spotify"),
        _entry("bravo", backend="ytmd"),
        _entry("alpha", backend="ytmd"),
    ]
    groups = build_groups(entries)
    assert [g.backend for g in groups] == ["spotify", "ytmd"]
    assert [e.name for e in groups[1].entries] == ["alpha", "bravo"]


def test_filter_requires_every_term_to_match():
    entries = [
        _entry("skip_song", summary="Skip to the next track", phrases=("next", "skip")),
        _entry("volume_up", summary="Raise the volume", phrases=("volume up",)),
    ]
    assert [e.name for g in build_groups(entries, "next") for e in g.entries] == ["skip_song"]
    assert [e.name for g in build_groups(entries, "skip track") for e in g.entries] == ["skip_song"]
    assert build_groups(entries, "skip volume") == []


def test_empty_query_keeps_everything():
    entries = [_entry("a"), _entry("b")]
    assert sum(len(g.entries) for g in build_groups(entries, "   ")) == 2


def test_inactive_entries_can_be_hidden():
    entries = [_entry("live"), _entry("dead", active=False)]
    kept = [e.name for g in build_groups(entries, include_inactive=False) for e in g.entries]
    assert kept == ["live"]


def test_group_label_reports_mixed_activity():
    both = build_groups([_entry("a"), _entry("b", active=False)])[0]
    assert "1 of 2 active" in group_label(both)
    assert group_label(build_groups([_entry("a")])[0]).endswith("1 command · active")
    assert group_label(build_groups([_entry("a", active=False)])[0]).endswith("inactive")


def test_patterns_are_used_when_an_action_has_no_examples():
    spec = ActionSpec(
        name="demo",
        capability="demo",
        backend="test",
        slot=None,
        transport="os_native",
        summary="Demo action",
        patterns=("^do the thing$",),
        handler=lambda: None,
        args=(ArgSpec("amount", "int", "how much"),),
    )
    entry = entry_from_spec(spec, active=True)
    assert entry.phrases == ("^do the thing$",)
    assert entry.args == ("amount",)


def test_every_registered_action_renders():
    entries = registry_entries()
    assert entries, "action registry is empty"
    for entry in entries:
        assert entry.phrases, f"{entry.name} has neither examples nor patterns"
        assert entry.summary, f"{entry.name} has no summary"
