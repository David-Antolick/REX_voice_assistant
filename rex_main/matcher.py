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
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from typing import Any, Callable

from rex_main import actions
from rex_main.actions.registry import on_rebuild
from rex_main.metrics import metrics

if TYPE_CHECKING:
    from rex_main.wake_word import ListeningState

__all__ = [
    "dispatch_command",
    "dispatch_text",
    "DispatchResult",
    "COMMAND_PATTERNS",
    "NO_EARLY_MATCH_COMMANDS",
]

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


# The single dispatch seam
#
# Both dispatch paths funnel through dispatch_text(): the standard
# VAD -> Whisper -> matcher pipeline (dispatch_command below) and the
# low-latency FastVAD path. They used to carry separate copies of this
# logic, which drifted — see docs/PHASE0_DISPATCH.md.

@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one dispatch attempt.

    FastVAD needs more than a boolean: it decides whether to keep
    buffering audio (``deferred``) or drop it (``executed`` /
    ``suppressed``) based on what happened here.
    """

    matched: bool = False
    action: Optional[str] = None
    executed: bool = False
    deferred: bool = False       # matched, but no_early_match on an early pass
    suppressed: bool = False     # matched, but the wake gate was closed
    exec_ms: Optional[float] = None


def dispatch_text(
    text: str,
    *,
    listening_state: "Optional[ListeningState]" = None,
    ui_callback: "Optional[Callable[..., None]]" = None,
    paused: "Optional[Any]" = None,
    early: bool = False,
) -> DispatchResult:
    """Match ``text`` against the active actions and run the handler.

    Args:
        early: True when called on a partial transcription. Early passes
            skip ``no_early_match`` actions and stay silent on no-match.
            FastVAD re-transcribes every ~200ms mid-utterance, so emitting
            a no-match event per partial would strobe the HUD's "didn't
            catch that" throughout every sentence. Only the final pass
            reports a miss.
    """
    text = text.strip()

    if paused is not None and paused.is_set():
        return DispatchResult()

    for pattern, action_name, handler in _DISPATCH_TABLE:
        m = pattern.match(text)
        if not m:
            continue

        # Ordered as the FastVAD path had it: early-eligibility before the
        # gate check, so a deferred command doesn't burn a suppression metric.
        if early and action_name in NO_EARLY_MATCH_COMMANDS:
            logger.debug("Deferring '%s' - requires full utterance", action_name)
            return DispatchResult(matched=True, action=action_name, deferred=True)

        if listening_state is not None and not listening_state.is_active():
            # info, not debug: "command didn't fire" is the single most common
            # support question, and this is the first thing to check.
            # See the /rex-diagnose-dispatch skill.
            logger.info(
                "Command '%s' suppressed from %r - wake word not active", action_name, text
            )
            metrics.record_command_suppressed(action_name)
            return DispatchResult(matched=True, action=action_name, suppressed=True)

        if listening_state is not None:
            # Refresh the window so multi-step interactions work without re-waking.
            listening_state.activate()

        logger.info("Matched action '%s'", action_name)
        metrics.record_command_match(action_name, matched=True)
        _emit(ui_callback, "match", action=action_name, text=text, args=m.groups())
        exec_ms = _invoke(action_name, handler, m.groups())
        return DispatchResult(
            matched=True, action=action_name, executed=True, exec_ms=exec_ms
        )

    if not early:
        logger.debug("No command matched for input: %r", text)
        metrics.record_command_match(None, matched=False)
        _emit(ui_callback, "no_match", text=text)
    return DispatchResult()


# Public coroutine

async def dispatch_command(
    text_queue: "asyncio.Queue[str]",
    listening_state: "Optional[ListeningState]" = None,
    ui_callback: "Optional[Callable[..., None]]" = None,
    paused: "Optional[Any]" = None,
):
    """Forever task that reads recognised text and triggers handlers.

    Standard-mode path. Every utterance arriving here is a completed one,
    so it always dispatches as a final pass (``early=False``).
    """
    logger.info("dispatch_command started - awaiting recognized text")

    while True:
        text = await text_queue.get()
        logger.debug("Received text: %s", text)
        dispatch_text(
            text,
            listening_state=listening_state,
            ui_callback=ui_callback,
            paused=paused,
        )
        text_queue.task_done()


# Helpers

def _emit(ui_callback: "Optional[Callable[..., None]]", event: str, **payload: Any) -> None:
    """Fire a UI event, never letting a UI bug reach the dispatch path."""
    if ui_callback is None:
        return
    try:
        ui_callback(event, **payload)
    except Exception:
        logger.exception("ui_callback raised on %s event", event)


def _invoke(
    action_name: str, handler: Callable[..., Any], args: tuple[str, ...]
) -> Optional[float]:
    """Run a handler. Returns elapsed ms, or None if it raised.

    This is the assistant's only error boundary around action handlers —
    an exception here must not escape into the audio loop that called us.
    """
    t0 = time.perf_counter()
    try:
        handler(*args)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error while executing %r: %s", action_name, exc)
        return None
    dt = (time.perf_counter() - t0) * 1000
    metrics.record_command_execute(action_name, dt)
    return dt
