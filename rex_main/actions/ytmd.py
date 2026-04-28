"""YouTube Music Desktop actions.

Talks to the YTMD Companion-Server over local HTTP.
See: https://github.com/ytmdesktop/ytmdesktop/wiki/v2-%E2%80%90-Companion-Server-API-v1
(YTMD's v2 API confusingly still has v1 in the URL.)
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Optional, TypeVar

import requests
from ytmusicapi import YTMusic

from rex_main.actions.registry import ArgSpec, action

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def safe_call(func: F) -> F:
    """Swallow transient network / API errors so one bad call can't kill the assistant."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.error("Network error in %s: %s", func.__name__, e)
        except Exception as e:
            logger.exception("Unexpected error in %s: %s", func.__name__, e)
        return None
    return wrapper  # type: ignore[return-value]


class YTMD:
    """Thin client for YT Music Desktop Companion-Server (POST /api/v1/command)."""

    def __init__(
        self,
        host: str | None = None,
        port: str | None = None,
        token: str | None = None,
        timeout: int = 5,
    ) -> None:
        # Default to 127.0.0.1 — host.docker.internal resolution can take
        # ~2s per call on native Windows because it falls through DNS +
        # NetBIOS before landing on the loopback. Docker users can still
        # set YTMD_HOST=host.docker.internal explicitly.
        raw_host = host or os.getenv("YTMD_HOST", "127.0.0.1")
        # Coerce "localhost" to "127.0.0.1" to dodge the Windows IPv6
        # fallback: Windows tries ::1 first, YTMD only listens on IPv4,
        # connection times out, then falls back — adding ~2s per call.
        self.host = "127.0.0.1" if raw_host.lower() == "localhost" else raw_host
        self.port = port or os.getenv("YTMD_PORT", "9863")
        self.token = token or os.getenv("YTMD_TOKEN")
        self.timeout = timeout

        self._base_url = f"http://{self.host}:{self.port}/api/v1/command"
        self._headers = {"Content-Type": "application/json"}
        if self.token:
            self._headers["Authorization"] = self.token

        # Reuse one HTTP connection across calls (keep-alive). Saves
        # ~5ms TCP setup per command and avoids per-call DNS lookup.
        self._session = requests.Session()
        self._session.headers.update(self._headers)

    def _send(self, command: str, *, value: Optional[Any] = None) -> None:
        payload: dict[str, Any] = {"command": command}
        if value is not None:
            payload["data"] = value

        try:
            r = self._session.post(
                self._base_url,
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error("YTMD command %r timed out after %ss", command, self.timeout)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "??"
            logger.error("YTMD command %r failed: HTTP %s", command, status)
        except requests.exceptions.RequestException as e:
            logger.error("YTMD command %r connection error: %s", command, e)
        else:
            logger.debug("YTMD: %s (%s)", command, value)

    @safe_call
    def search_song(self, title: str, artist: str | None = None) -> None:
        """Search YouTube Music for "title [+ artist]" and play the first match."""
        query = f"{title} by {artist}" if artist else title
        ytm = YTMusic()
        results = ytm.search(query, filter="songs", limit=1)

        if not results:
            logger.error("No YTM results for %r", query)
            return

        video_id = results[0].get("videoId")
        if not video_id:
            logger.error("Search hit with no videoId: %r", results[0])
            return

        self._send("changeVideo", value={"videoId": video_id, "playlistId": None})
        logger.info("YTMD playing videoId %s", video_id)

    def play_music(self):
        self._send("play")

    def stop_music(self):
        self._send("pause")

    def next_track(self):
        self._send("next")

    def previous_track(self):
        self._send("seekTo", value=4)
        self._send("previous")

    def restart_track(self):
        self._send("seekTo", value=5)
        self._send("previous")

    def volume_up(self):
        self._send("volumeUp")

    def volume_down(self):
        self._send("volumeDown")

    def set_volume(self, level: int | str) -> None:
        try:
            vol = max(0, min(100, int(level)))
        except (ValueError, TypeError):
            logger.error("Bad volume value: %s", level)
            return
        self._send("setVolume", value=vol)

    def like(self):
        self._send("toggleLike")

    def dislike(self):
        self._send("toggleDislike")

    def so_sad(self):
        self._send("changeVideo", value={"videoId": "FdMG84qN_98", "playlistId": None})
        logger.info("YTMD playing videoId %s", "FdMG84qN_98")


# Lazy singleton — only instantiate when the user actually invokes an action.
_client: YTMD | None = None


def _get() -> YTMD:
    global _client
    if _client is None:
        _client = YTMD()
    return _client


def reset_client() -> None:
    """Drop the cached client so env-var changes take effect on next call."""
    global _client
    _client = None


# Action registrations
# Common pattern fragments — keep aligned with rex_main/actions/spotify.py.
_END = r"[.!?\s]*$"
_W = r"\s*"

_BACKEND = "ytmd"
_TRANSPORT = "http_local"
_SLOT = "music"
_PRECONDS = ("YTMD desktop app running with Companion-Server enabled",)


@action(
    name="ytmd_play_music",
    capability="play_music",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Resume YouTube Music playback.",
    patterns=[rf"^{_W}play\s+music{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state",),
    examples=("play music",),
)
def play_music() -> None:
    _get().play_music()


@action(
    name="ytmd_stop_music",
    capability="stop_music",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Pause YouTube Music playback.",
    patterns=[rf"^{_W}stop\s+music{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state",),
    examples=("stop music",),
)
def stop_music() -> None:
    _get().stop_music()


@action(
    name="ytmd_next_track",
    capability="next_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Skip to the next track in the YTMD queue.",
    patterns=[rf"^{_W}(?:next|skip){_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("current_track",),
    examples=("next", "skip"),
)
def next_track() -> None:
    _get().next_track()


@action(
    name="ytmd_previous_track",
    capability="previous_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Go back to the previous track.",
    patterns=[rf"^{_W}(?:last|previous){_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("current_track",),
    examples=("last", "previous"),
)
def previous_track() -> None:
    _get().previous_track()


@action(
    name="ytmd_restart_track",
    capability="restart_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Restart the current track from the beginning.",
    patterns=[rf"^{_W}restart{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("track_position",),
    examples=("restart",),
)
def restart_track() -> None:
    _get().restart_track()


@action(
    name="ytmd_search_song",
    capability="search_song",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Search YouTube Music for a track and play the first match.",
    patterns=[rf"^{_W}search\s+(.+?)(?:\s+by\s+(.+?))?{_END}"],
    args=(
        ArgSpec("title", "str", "Song title to search for."),
        ArgSpec("artist", "str", "Optional artist name."),
    ),
    preconditions=_PRECONDS,
    side_effects=("playback_state", "current_track"),
    examples=("search bohemian rhapsody", "search hotel california by eagles"),
    no_early_match=True,
)
def search_song(title: str, artist: str | None = None) -> None:
    _get().search_song(title, artist)


@action(
    name="ytmd_volume_up",
    capability="volume_up",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Step YouTube Music volume up.",
    patterns=[rf"^{_W}volume up{_END}"],
    preconditions=_PRECONDS,
    side_effects=("volume",),
    examples=("volume up",),
    no_early_match=True,
)
def volume_up() -> None:
    _get().volume_up()


@action(
    name="ytmd_volume_down",
    capability="volume_down",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Step YouTube Music volume down.",
    patterns=[rf"^{_W}volume down{_END}"],
    preconditions=_PRECONDS,
    side_effects=("volume",),
    examples=("volume down",),
    no_early_match=True,
)
def volume_down() -> None:
    _get().volume_down()


@action(
    name="ytmd_set_volume",
    capability="set_volume",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Set YouTube Music volume to a specific 0–100 level.",
    patterns=[rf"^{_W}volume\s+(\d{{1,3}}){_W}{_END}"],
    args=(ArgSpec("level", "int", "Target volume 0–100 (clamped)."),),
    preconditions=_PRECONDS,
    side_effects=("volume",),
    examples=("volume 50", "volume 75"),
    no_early_match=True,
)
def set_volume(level: int | str) -> None:
    _get().set_volume(level)


@action(
    name="ytmd_like",
    capability="like",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Toggle the like (thumbs-up) on the current track.",
    patterns=[rf"^{_W}like{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("track_rating",),
    examples=("like",),
)
def like() -> None:
    _get().like()


@action(
    name="ytmd_dislike",
    capability="dislike",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Toggle the dislike (thumbs-down) on the current track.",
    patterns=[rf"^{_W}dislike{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("track_rating",),
    examples=("dislike",),
)
def dislike() -> None:
    _get().dislike()


@action(
    name="ytmd_so_sad",
    capability="so_sad",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Easter egg — play the 'so sad' track on YTMD.",
    patterns=[rf"^{_W}this\s+is\s+so\s+sad{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state", "current_track"),
    examples=("this is so sad",),
)
def so_sad() -> None:
    _get().so_sad()
