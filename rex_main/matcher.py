"""
matcher.py
Regex-based command dispatcher for the REX assistant.

Usage inside your main program (rex.py):

    text_q = asyncio.Queue()
    asyncio.create_task(dispatch_command(text_q))

Each message pulled from *text_q* is compared against the patterns below.
On the first match the corresponding function inside *commands.py* is
invoked (synchronously for now; wrap in run_in_executor if commands turn
CPU-heavy).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Optional, TYPE_CHECKING

import rex_main.commands as commands
import rex_main.steelseries as steelseries
from rex_main.metrics import metrics

if TYPE_CHECKING:
    from rex_main.wake_word import ListeningState

__all__ = ["dispatch_command", "COMMAND_PATTERNS", "NO_EARLY_MATCH_COMMANDS"]

logger = logging.getLogger(__name__)

# Commands that should NOT use early matching (wait for full utterance)
# These are commands with variable arguments that could be cut off mid-speech
NO_EARLY_MATCH_COMMANDS: set[str] = {
    "search_song",      # "search X by Y" - would early-match on "search up" instead of "search upside down"
    "queue_track",      # "next track X" - same issue
    "volume_up",        # "volume up" early-matches before "volume up to 50"-style numeric variants land
    "volume_down",
    "set_volume",       # "volume 50" - wait for full number to avoid "volume 5" firing early
}


# Common helpers (for robustness)
_END   = r"[.!?\s]*$"              # trailing punctuation / spaces
_WORD  = r"\s*"                    # surrounding spaces

COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [

    # Music commands
    #  play / pause
    (re.compile(rf"^{_WORD}stop\s+music{_WORD}{_END}",  re.I), "stop_music"),
    (re.compile(rf"^{_WORD}play\s+music{_WORD}{_END}", re.I), "play_music"),


    #  track navigation
    (re.compile(rf"^{_WORD}(?:next|skip){_WORD}{_END}", re.I), "next_track"),
    (re.compile(rf"^{_WORD}(?:last|previous){_WORD}{_END}", re.I), "previous_track"),
    (re.compile(rf"^{_WORD}restart{_WORD}{_END}", re.I), "restart_track"),
    (re.compile(rf"^{_WORD}search\s+(.+?)(?:\s+by\s+(.+?))?{_END}", re.I), "search_song"),


    #  volume control
    (re.compile(rf"^{_WORD}volume up{_END}",   re.I), "volume_up"),
    (re.compile(rf"^{_WORD}volume down{_END}", re.I), "volume_down"),
    (re.compile(rf"^{_WORD}volume\s+(\d{{1,3}}){_WORD}{_END}", re.I), "set_volume"),


    #  like / dislike
    (re.compile(rf"^{_WORD}like{_WORD}{_END}",    re.I), "like"),
    (re.compile(rf"^{_WORD}dislike{_WORD}{_END}", re.I), "dislike"),

    #  Others commands
    (re.compile(rf"^{_WORD}this\s+is\s+so\s+sad{_WORD}{_END}", re.I), "so_sad"),
    (re.compile(rf"^{_WORD}shuffle\s+on{_END}", re.I),  "shuffle_on"),
    (re.compile(rf"^{_WORD}shuffle\s+off{_END}", re.I), "shuffle_off"),
    (re.compile(rf"^{_WORD}repeat\s+(off|context|track){_END}", re.I), "set_repeat"),       # captures “off”, “context”, or “track”)
    (re.compile(rf"^{_WORD}next\s+track(?:\s*[,;:]\s*|\s+)(.+?){_END}", re.I), "queue_track"),  # Queue a specific URI (e.g. “next track (song name)”)
    (re.compile(rf"^{_WORD}(?:what(?:'s)?\s+playing|current\s+track\s+info|track\s+info){_END}",re.I), "current_track_info"),


    #  Switching music apps (spotify, youtube music)
    (re.compile(rf"^{_WORD}switch\s+to\s+spotify{_END}", re.I), "configure_spotify"),
    (re.compile(rf"^{_WORD}switch\s+to\s+youtube\s+music{_END}", re.I), "configure_ytmd"),

    # Clipping (SteelSeries Moments)
    # Multiple phrases for better recognition - "capture" and "record" have harder consonants
    (re.compile(rf"^{_WORD}(?:clip\s+(?:that|it)|save\s+(?:that|clip)|capture\s+(?:that|it)|record\s+(?:that|clip)){_END}", re.I), "clip_that"),
]


# Public coroutine
async def dispatch_command(
    text_queue: "asyncio.Queue[str]",
    listening_state: "Optional[ListeningState]" = None,
):
    """Forever task that reads recognised text and triggers handlers.

    If `listening_state` is provided and its gate is enabled, commands are only
    invoked when the listening window is active (post wake-word).
    """
    logger.info("dispatch_command started - awaiting recognized text")

    while True:
        text = (await text_queue.get()).strip()
        logger.debug("Received text: %s", text)

        matched = False
        for pattern, func_name in COMMAND_PATTERNS:
            m = pattern.match(text)
            if m:
                matched = True
                if listening_state is not None and not listening_state.is_active():
                    logger.debug("Command '%s' suppressed - wake word not active", func_name)
                    metrics.record_command_suppressed(func_name)
                    break
                if listening_state is not None:
                    listening_state.activate()
                logger.info("Matched command '%s'", func_name)
                # Record match for metrics
                metrics.record_command_match(func_name, matched=True)
                _call_handler(func_name, m.groups())
                break

        if not matched:
            logger.debug("No command matched for input: %r", text)
            # Record unmatched for metrics
            metrics.record_command_match(None, matched=False)

        text_queue.task_done()


# Helpers

def _call_handler(func_name: str, args: tuple[str, ...]):
    """Look up handler in commands or steelseries module and invoke it with *args*."""

    # Try commands module first, then steelseries module
    func: Callable[..., None] | None = getattr(commands, func_name, None)
    if func is None:
        func = getattr(steelseries, func_name, None)
    if not callable(func):
        logger.error("Handler '%s' not found in commands.py or steelseries.py", func_name)
        return

    try:
        t0 = time.perf_counter()
        func(*args)
        dt = (time.perf_counter() - t0) * 1000
        # Record execution time for metrics
        metrics.record_command_execute(func_name, dt)
        # Note: benchmark.record_command is called from whisper_worker after full pipeline
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error while executing '%s': %s", func_name, exc)
