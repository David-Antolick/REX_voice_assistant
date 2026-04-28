"""Action registry package.

Single source of truth for every voice-triggered action in REX.
See docs/ACTIONS.md for the pattern, rules, and full inventory.
"""

from __future__ import annotations

from rex_main.actions.registry import (
    ActionSpec,
    ArgSpec,
    action,
    active_specs,
    all_specs,
    find_by_name,
    is_active,
    resolve_handler,
    set_active_backend,
    set_active_backends,
)

# Importing each backend module triggers @action registrations.
# Backend modules first, then service.py (which references the backends).
from rex_main.actions import discord  # noqa: F401
from rex_main.actions import spotify  # noqa: F401
from rex_main.actions import steelseries  # noqa: F401
from rex_main.actions import ytmd  # noqa: F401
from rex_main.actions import service  # noqa: F401  # imports ytmd + spotify modules

__all__ = [
    "ActionSpec",
    "ArgSpec",
    "action",
    "active_specs",
    "all_specs",
    "find_by_name",
    "is_active",
    "resolve_handler",
    "set_active_backend",
    "set_active_backends",
]
