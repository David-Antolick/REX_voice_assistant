"""Registry of voice-triggered actions.

Every action REX can perform is declared with the @action decorator and
described by an ActionSpec. The matcher compiles the active subset into
its regex table, and a future planning layer can introspect the registry
to choose which actions to chain for a higher-level task.

See docs/ACTIONS.md for the authoring rules and full inventory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class ArgSpec:
    """One captured argument for an action."""
    name: str
    type: str            # "str" | "int" | "enum:a|b|c"
    description: str


@dataclass(frozen=True)
class ActionSpec:
    """Everything REX (and future planners) need to know about one action."""
    name: str                                # globally unique, snake_case
    capability: str                          # abstract verb (e.g. "play_music")
    backend: str                             # "ytmd" | "spotify" | "steelseries" | ...
    slot: str | None                         # which service slot (None = always-on)
    transport: str                           # "http_local" | "oauth_cloud" | "os_native" | "gamesense"
    summary: str                             # one-line natural-language description
    patterns: tuple[str, ...]                # regex source strings
    handler: Callable[..., Any]              # function called when matched
    args: tuple[ArgSpec, ...] = ()
    preconditions: tuple[str, ...] = ()      # human-readable prerequisites
    side_effects: tuple[str, ...] = ()       # what state this changes
    examples: tuple[str, ...] = ()           # natural utterances
    no_early_match: bool = False             # wait for full utterance


# Module-level state

_REGISTRY: list[ActionSpec] = []
_BY_NAME: dict[str, ActionSpec] = {}     # O(1) lookup index
# slot -> backend currently providing that slot. Slots not in the dict have
# no active backend, so any spec with that slot is inactive.
_ACTIVE_BACKENDS: dict[str, str] = {}
_REBUILD_HOOKS: list[Callable[[], None]] = []


def _register(spec: ActionSpec) -> None:
    if spec.name in _BY_NAME:
        raise ValueError(f"Duplicate action name: {spec.name!r}")
    _REGISTRY.append(spec)
    _BY_NAME[spec.name] = spec


def action(
    *,
    name: str,
    capability: str,
    backend: str,
    transport: str,
    summary: str,
    patterns: list[str] | tuple[str, ...],
    slot: str | None = None,
    args: list[ArgSpec] | tuple[ArgSpec, ...] = (),
    preconditions: list[str] | tuple[str, ...] = (),
    side_effects: list[str] | tuple[str, ...] = (),
    examples: list[str] | tuple[str, ...] = (),
    no_early_match: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a function as a REX action."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        spec = ActionSpec(
            name=name,
            capability=capability,
            backend=backend,
            slot=slot,
            transport=transport,
            summary=summary,
            patterns=tuple(patterns),
            handler=fn,
            args=tuple(args),
            preconditions=tuple(preconditions),
            side_effects=tuple(side_effects),
            examples=tuple(examples),
            no_early_match=no_early_match,
        )
        _register(spec)
        return fn
    return decorator


# Active-backend management

def set_active_backend(slot: str, backend: str | None) -> None:
    """Set (or clear, with backend=None) the active backend for one slot."""
    if backend is None:
        _ACTIVE_BACKENDS.pop(slot, None)
    else:
        _ACTIVE_BACKENDS[slot] = backend
    _fire_rebuild_hooks()


def set_active_backends(mapping: dict[str, str | None]) -> None:
    """Replace the full slot -> backend mapping in one call."""
    for slot, backend in mapping.items():
        if backend is None:
            _ACTIVE_BACKENDS.pop(slot, None)
        else:
            _ACTIVE_BACKENDS[slot] = backend
    _fire_rebuild_hooks()


def is_active(spec: ActionSpec) -> bool:
    """An action is active if it has no slot, or its backend owns its slot."""
    if spec.slot is None:
        return True
    return _ACTIVE_BACKENDS.get(spec.slot) == spec.backend


# Lookups

def all_specs() -> list[ActionSpec]:
    return list(_REGISTRY)


def active_specs() -> list[ActionSpec]:
    return [s for s in _REGISTRY if is_active(s)]


def find_by_name(name: str) -> ActionSpec | None:
    return _BY_NAME.get(name)


def resolve_handler(name: str) -> Callable[..., Any] | None:
    spec = _BY_NAME.get(name)
    return spec.handler if spec else None


# Rebuild hooks (matcher subscribes so it can recompile patterns on swap)

def on_rebuild(hook: Callable[[], None]) -> None:
    _REBUILD_HOOKS.append(hook)


def _fire_rebuild_hooks() -> None:
    for hook in _REBUILD_HOOKS:
        try:
            hook()
        except Exception:
            logger.exception("Action-registry rebuild hook failed")
