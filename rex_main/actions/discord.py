"""Discord voice control via Windows UI Automation.

Invokes the bottom voice-panel buttons (Mute, Deafen, Disconnect) in the
Discord desktop client through the OS accessibility surface. No keystrokes,
no Discord RPC whitelist, no second session — just a held COM pointer per
button and a direct .invoke() call.

English Discord client only: button-name lookup is case- and locale-sensitive.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, TypeVar

from comtypes import COMError
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError

from rex_main.actions.registry import action

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# Hardcoded from the live UIA spike (_local/discord_uia_dump.py).
# These are the exact accessibility names of the bottom voice-panel buttons.
# Mute/Unmute and Deafen/Undeafen are the same DOM node renamed by Discord
# when state flips, so the held COM pointer survives the rename.
_WINDOW_TITLE_PATTERN = r".*Discord$"
_BTN_MIC = "Mute"
_BTN_DEAFEN = "Deafen"
_BTN_DISCONNECT = "Disconnect"


def safe_call(func: F) -> F:
    """Swallow UIA / COM errors so one bad call can't kill the assistant.

    Stale COM pointers (Discord restarted) and missing buttons (e.g. Disconnect
    when not in voice) both invalidate the cached wrapper for the affected
    button, so the next call re-materializes via one fresh FindFirst.
    """
    @functools.wraps(func)
    def wrapper(self: "DiscordClient", *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except ElementNotFoundError as e:
            self._invalidate_all()
            logger.warning("Discord UIA element not found in %s: %s", func.__name__, e)
        except COMError as e:
            self._invalidate_all()
            logger.warning("Discord UIA stale pointer in %s (Discord restarted?): %s", func.__name__, e)
        except Exception as e:
            self._invalidate_all()
            logger.exception("Unexpected error in %s: %s", func.__name__, e)
        return None
    return wrapper  # type: ignore[return-value]


class DiscordClient:
    """Holds COM pointers to the voice-panel buttons. One FindFirst per button, ever."""

    def __init__(self) -> None:
        self._desktop = Desktop(backend="uia")
        self._window: Any = None
        self._buttons: dict[str, Any] = {}

    def _get_window(self) -> Any:
        # Lazy WindowSpecification — child_window() lives on the spec, not
        # on the materialized wrapper. The spec caches its resolved element
        # internally on first use.
        if self._window is None:
            self._window = self._desktop.window(title_re=_WINDOW_TITLE_PATTERN)
        return self._window

    def _resolve(self, name: str) -> Any:
        if name in self._buttons:
            return self._buttons[name]

        # Deafen is globally unique in Discord's accessibility tree (the
        # channel-header banner has Mute / Disconnect / soundboard / camera
        # / screen-share but no Deafen). So we resolve Deafen directly and
        # use it as the anchor for the duplicated buttons.
        if name == _BTN_DEAFEN:
            self._buttons[name] = self._get_window().child_window(
                title=name, control_type="Button"
            ).wrapper_object()
            return self._buttons[name]

        # Mute / Disconnect: when in a voice channel, both the bottom voice
        # panel and the channel header expose them. Find them as immediate
        # siblings of the (unique) Deafen button — that scopes us to the
        # bottom panel. children() is a single UIA GetChildren on a small
        # container (~3-5 buttons).
        deafen = self._resolve(_BTN_DEAFEN)
        for child in deafen.parent().children(control_type="Button"):
            if (child.window_text() or "").strip() == name:
                self._buttons[name] = child
                return child
        raise ElementNotFoundError(f"'{name}' not found among Deafen's siblings")

    def _invoke(self, name: str) -> None:
        self._resolve(name).invoke()
        logger.info("Discord: invoked %r", name)

    def _invalidate_all(self) -> None:
        self._window = None
        self._buttons.clear()

    def warm(self) -> int:
        """Pre-resolve mic + deafen button pointers. Returns number cached.

        Skips Disconnect since it only exists when in a voice channel.
        Caller is expected to swallow ElementNotFoundError if Discord
        isn't running.
        """
        for btn in (_BTN_MIC, _BTN_DEAFEN):
            self._resolve(btn)
        return len(self._buttons)

    @safe_call
    def mute_toggle(self) -> None:
        self._invoke(_BTN_MIC)

    @safe_call
    def deafen_toggle(self) -> None:
        self._invoke(_BTN_DEAFEN)

    @safe_call
    def disconnect(self) -> None:
        self._invoke(_BTN_DISCONNECT)


# Lazy singleton

_client: Optional[DiscordClient] = None


def _get() -> DiscordClient:
    global _client
    if _client is None:
        _client = DiscordClient()
    return _client


def reset_client() -> None:
    global _client
    _client = None


def warm() -> None:
    """Pre-resolve voice-panel buttons so the first voice command is fast.

    No-op if Discord isn't running — the cache stays empty and the first
    in-flight command will resolve on demand.
    """
    try:
        n = _get().warm()
        logger.info("Discord: pre-warmed %d button(s)", n)
    except ElementNotFoundError:
        logger.info("Discord not running at startup — will warm cache on first command")
    except Exception as e:
        logger.debug("Could not pre-warm Discord: %s", e)


# Action registrations

_END = r"[.!?\s]*$"
_W = r"\s*"
_BACKEND = "discord"
_SLOT = None  # always-on; Discord is currently the only voice_chat backend
_PRECONDS = ("Discord desktop app running",)


@action(
    name="discord_mute_toggle",
    capability="toggle_mic",
    backend=_BACKEND,
    slot=_SLOT,
    transport="os_native",
    summary="Toggle Discord microphone mute.",
    # tiny.en often hears "unmute" as "on mute" (and similar). Accept the variants.
    patterns=[rf"^{_W}(?:un|on|an|a|in)?\s*mute(?:d)?{_END}"],
    preconditions=_PRECONDS,
    side_effects=("voice_mute_state",),
    examples=("mute", "unmute", "on mute"),
)
def mute_toggle() -> None:
    _get().mute_toggle()


@action(
    name="discord_deafen_toggle",
    capability="toggle_deafen",
    backend=_BACKEND,
    slot=_SLOT,
    transport="os_native",
    summary="Toggle Discord deafen.",
    # tiny.en hears "deafen" as "daffin" / "deafin" / "deaf" (drops -en),
    # and "undeafen" as "on deafen" / "on deafened". Accept the variants.
    patterns=[rf"^{_W}(?:un|on|an|a|in)?\s*(?:deaf|daff)(?:en|in|ened|ine)?{_END}"],
    preconditions=_PRECONDS,
    side_effects=("voice_deafen_state",),
    examples=("deafen", "undeafen", "on deafen", "daffin", "deaf"),
)
def deafen_toggle() -> None:
    _get().deafen_toggle()


@action(
    name="discord_disconnect",
    capability="disconnect_voice",
    backend=_BACKEND,
    slot=_SLOT,
    transport="os_native",
    summary="Disconnect from Discord voice channel.",
    patterns=[rf"^{_W}leave\s+channel{_END}"],
    preconditions=("Discord desktop app running", "User in a voice channel"),
    side_effects=("voice_connection",),
    examples=("leave channel",),
)
def disconnect() -> None:
    _get().disconnect()
