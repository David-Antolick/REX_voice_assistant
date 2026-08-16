"""discovery.py
CommandsDialog — the read-only "what can I say?" cheat sheet.

Built from the action registry every time it opens, so it cannot drift
from what REX actually understands. Nothing here is hardcoded per
backend: a new backend module shows up the moment it registers actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rex_main import actions
from rex_main.actions.registry import ActionSpec
from rex_main.ui.icons import make_app_icon


@dataclass(frozen=True)
class CommandEntry:
    """One row of the cheat sheet: what to say, what it does, is it live."""
    name: str
    backend: str
    summary: str
    phrases: tuple[str, ...]
    args: tuple[str, ...]
    active: bool


@dataclass(frozen=True)
class BackendGroup:
    backend: str
    entries: tuple[CommandEntry, ...]

    @property
    def active_count(self) -> int:
        return sum(1 for e in self.entries if e.active)


def entry_from_spec(spec: ActionSpec, active: bool) -> CommandEntry:
    # Examples are the user-facing phrasing. Regex sources are the fallback so
    # an action authored without examples still lists, rather than showing blank.
    return CommandEntry(
        name=spec.name,
        backend=spec.backend,
        summary=spec.summary,
        phrases=tuple(spec.examples or spec.patterns),
        args=tuple(a.name for a in spec.args),
        active=active,
    )


def registry_entries() -> list[CommandEntry]:
    return [entry_from_spec(s, actions.is_active(s)) for s in actions.all_specs()]


def matches(entry: CommandEntry, query: str) -> bool:
    """All whitespace-separated terms must appear somewhere in the entry."""
    haystack = " ".join((entry.name, entry.backend, entry.summary, *entry.phrases)).lower()
    return all(term in haystack for term in query.lower().split())


def build_groups(
    entries: Iterable[CommandEntry],
    query: str = "",
    include_inactive: bool = True,
) -> list[BackendGroup]:
    """Filter by query, then group by backend. Backends and rows sorted by name."""
    by_backend: dict[str, list[CommandEntry]] = {}
    for entry in entries:
        if not include_inactive and not entry.active:
            continue
        if not matches(entry, query):
            continue
        by_backend.setdefault(entry.backend, []).append(entry)
    return [
        BackendGroup(backend, tuple(sorted(rows, key=lambda e: e.name)))
        for backend, rows in sorted(by_backend.items())
    ]


def group_label(group: BackendGroup) -> str:
    total = len(group.entries)
    active = group.active_count
    if active == total:
        state = "active"
    elif active == 0:
        state = "inactive"
    else:
        state = f"{active} of {total} active"
    return f"{group.backend} — {total} command{'' if total == 1 else 's'} · {state}"


def entry_tooltip(entry: CommandEntry) -> str:
    lines = [entry.name]
    if entry.args:
        lines.append("takes: " + ", ".join(entry.args))
    if not entry.active:
        lines.append(f"inactive — {entry.backend} is not the selected backend for this slot")
    return "\n".join(lines)


class CommandsDialog(QDialog):
    """Read-only. Never mutates the registry, the config, or the runtime."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rex — What can I say?")
        self.setWindowIcon(make_app_icon())
        self.resize(680, 560)

        self._entries: list[CommandEntry] = []

        layout = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter commands…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._populate)
        filter_row.addWidget(self._search, 1)
        self._show_inactive = QCheckBox("Show inactive")
        self._show_inactive.setChecked(True)
        self._show_inactive.setToolTip(
            "Inactive commands belong to a backend that isn't currently selected\n"
            "for its slot — e.g. Spotify while YTMD is the active music service."
        )
        self._show_inactive.toggled.connect(self._populate)
        filter_row.addWidget(self._show_inactive)
        layout.addLayout(filter_row)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Say", "What it does"])
        self._tree.setUniformRowHeights(True)
        layout.addWidget(self._tree, 1)

        self._status = QLabel()
        self._status.setStyleSheet("color: gray;")
        layout.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh()

    def refresh(self) -> None:
        """Re-read the registry. Cheap, and picks up a live service switch."""
        self._entries = registry_entries()
        self._populate()

    def _populate(self) -> None:
        groups = build_groups(
            self._entries,
            self._search.text(),
            self._show_inactive.isChecked(),
        )
        dimmed = self.palette().color(QPalette.Disabled, QPalette.WindowText)

        self._tree.clear()
        shown = 0
        for group in groups:
            head = QTreeWidgetItem(self._tree, [group_label(group)])
            head.setFirstColumnSpanned(True)
            font = head.font(0)
            font.setBold(True)
            head.setFont(0, font)
            for entry in group.entries:
                item = QTreeWidgetItem(head, [" · ".join(entry.phrases), entry.summary])
                item.setToolTip(0, entry_tooltip(entry))
                item.setToolTip(1, entry.summary)
                if not entry.active:
                    item.setForeground(0, dimmed)
                    item.setForeground(1, dimmed)
                shown += 1
        self._tree.expandAll()
        self._tree.resizeColumnToContents(0)

        total = len(self._entries)
        active = sum(1 for e in self._entries if e.active)
        if shown == total:
            self._status.setText(f"{total} commands · {active} active")
        else:
            self._status.setText(f"{shown} of {total} commands shown · {active} active")
