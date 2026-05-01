"""matcher.py
Regex-based command dispatcher for the REX assistant.

Patterns and handlers are pulled live from the action registry
(`rex_main.actions`). This module just compiles the active subset and
runs the dispatch loop.

Usage inside rex.py:

    text_q = asyncio.Queue()
    asyncio.create_task(dispatch_command(text_q))
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional, TYPE_CHECKING

from typing import Any, Callable

from rex_main import actions
from rex_main.actions.registry import on_rebuild
from rex_main.metrics import metrics

if TYPE_CHECKING:
    from rex_main.wake_word import ListeningState

__all__ = ["dispatch_command", "COMMAND_PATTERNS", "NO_EARLY_MATCH_COMMANDS"]

logger = logging.getLogger(__name__)


# Compiled-pattern cache, rebuilt whenever the active backend set changes.
# Each entry is (compiled_regex, action_name) for back-compat with the
# rex.py FastVAD path. _DISPATCH_TABLE is the dispatcher's hot path and
# additionally carries the resolved handler so we don't look it up per match.
COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = []
# Action names that should NOT match early (wait for full utterance).
NO_EARLY_MATCH_COMMANDS: set[str] = set()
_DISPATCH_TABLE: list[tuple[re.Pattern[str], str, Callable[..., Any]]] = []


def _rebuild() -> None:
    """Recompile COMMAND_PATTERNS / _DISPATCH_TABLE / NO_EARLY_MATCH_COMMANDS."""
    new_patterns: list[tuple[re.Pattern[str], str]] = []
    new_table: list[tuple[re.Pattern[str], str, Callable[..., Any]]] = []
    new_no_early: set[str] = set()
    for spec in actions.active_specs():
        for src in spec.patterns:
            compiled = re.compile(src, re.I)
            new_patterns.append((compiled, spec.name))
            new_table.append((compiled, spec.name, spec.handler))
        if spec.no_early_match:
            new_no_early.add(spec.name)
    COMMAND_PATTERNS[:] = new_patterns
    _DISPATCH_TABLE[:] = new_table
    NO_EARLY_MATCH_COMMANDS.clear()
    NO_EARLY_MATCH_COMMANDS.update(new_no_early)
    logger.debug("Matcher rebuilt: %d patterns, %d no-early-match",
                 len(COMMAND_PATTERNS), len(NO_EARLY_MATCH_COMMANDS))


# Subscribe to registry rebuilds so service-switch actions take effect immediately.
on_rebuild(_rebuild)
_rebuild()


# Public coroutine

async def dispatch_command(
    text_queue: "asyncio.Queue[str]",
    listening_state: "Optional[ListeningState]" = None,
    ui_callback: "Optional[Callable[..., None]]" = None,
    paused: "Optional[Any]" = None,
):
    """Forever task that reads recognised text and triggers handlers."""
    logger.info("dispatch_command started - awaiting recognized text")

    while True:
        text = (await text_queue.get()).strip()
        logger.debug("Received text: %s", text)

        if paused is not None and paused.is_set():
            text_queue.task_done()
            continue

        matched = False
        for pattern, action_name, handler in _DISPATCH_TABLE:
            m = pattern.match(text)
            if m:
                matched = True
                if listening_state is not None and not listening_state.is_active():
                    logger.debug("Command '%s' suppressed - wake word not active", action_name)
                    metrics.record_command_suppressed(action_name)
                    break
                if listening_state is not None:
                    listening_state.activate()
                logger.info("Matched action '%s'", action_name)
                metrics.record_command_match(action_name, matched=True)
                if ui_callback is not None:
                    try:
                        ui_callback("match", action=action_name, text=text, args=m.groups())
                    except Exception:
                        logger.exception("ui_callback raised on match event")
                _invoke(action_name, handler, m.groups())
                break

        if not matched:
            logger.debug("No command matched for input: %r", text)
            metrics.record_command_match(None, matched=False)
            if ui_callback is not None:
                try:
                    ui_callback("no_match", text=text)
                except Exception:
                    logger.exception("ui_callback raised on no_match event")

        text_queue.task_done()


# Helpers

def _invoke(action_name: str, handler: Callable[..., Any], args: tuple[str, ...]):
    try:
        t0 = time.perf_counter()
        handler(*args)
        dt = (time.perf_counter() - t0) * 1000
        metrics.record_command_execute(action_name, dt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error while executing %r: %s", action_name, exc)
