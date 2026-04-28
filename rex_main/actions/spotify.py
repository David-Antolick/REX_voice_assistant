"""Spotify actions.

Talks to Spotify via the Web API + Connect (OAuth user-auth flow).
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Callable, TypeVar

import requests
from spotipy import Spotify
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from rex_main.actions.registry import ArgSpec, action

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def safe_call(func: F) -> F:
    """Swallow transient network / API errors so one bad call can't kill the assistant."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SpotifyException as e:
            logger.error("Spotify API error in %s: HTTP %s — %s", func.__name__, e.http_status, e.msg)
        except requests.exceptions.RequestException as e:
            logger.error("Network error in %s: %s", func.__name__, e)
        except Exception as e:
            logger.exception("Unexpected error in %s: %s", func.__name__, e)
        return None
    return wrapper  # type: ignore[return-value]


class SpotifyClient:
    """Control your desktop Spotify app via the Spotify Web API / Connect."""
    SCOPE = (
        "user-modify-playback-state "
        "user-read-playback-state "
        "user-library-modify "
        "user-library-read"
    )

    def __init__(self):
        cache_path = Path.home() / ".rex" / "spotify_token.cache"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        auth = SpotifyOAuth(
            scope=self.SCOPE,
            open_browser=True,
            cache_path=str(cache_path),
        )
        self.sp = Spotify(auth_manager=auth)

        devices = self.sp.devices().get("devices", [])
        if not devices:
            raise RuntimeError("No Spotify Connect devices found.")
        self.device_id = next(
            (d["id"] for d in devices if "Computer" in d["name"]),
            devices[0]["id"],
        )
        logger.info("Using Spotify Connect device %r", self.device_id)

    @safe_call
    def search_song(self, title: str, artist: str | None = None) -> None:
        query = f"{title} {artist or ''}".strip()
        results = self.sp.search(q=query, type="track", limit=1).get("tracks", {}).get("items", [])
        if not results:
            logger.error("Spotify search found no tracks for %r", query)
            return
        track_uri = results[0]["uri"]
        self.sp.start_playback(device_id=self.device_id, uris=[track_uri])
        logger.info("Spotify playing %s", track_uri)

    @safe_call
    def play_music(self):
        self.sp.start_playback(device_id=self.device_id)
        logger.info("Spotify: play")

    @safe_call
    def stop_music(self):
        self.sp.pause_playback(device_id=self.device_id)
        logger.info("Spotify: pause")

    @safe_call
    def next_track(self):
        self.sp.next_track(device_id=self.device_id)
        logger.info("Spotify: next")

    @safe_call
    def previous_track(self):
        self.sp.previous_track(device_id=self.device_id)
        logger.info("Spotify: previous")

    @safe_call
    def restart_track(self) -> None:
        self.sp.seek_track(position_ms=0, device_id=self.device_id)
        logger.info("Spotify restart")

    @safe_call
    def volume_up(self) -> None:
        current = self.sp.current_playback().get("device", {}).get("volume_percent", 50)
        new = min(100, current + 10)
        self.sp.volume(new, device_id=self.device_id)
        logger.info("Spotify volume set to %d%%", new)

    @safe_call
    def volume_down(self) -> None:
        current = self.sp.current_playback().get("device", {}).get("volume_percent", 50)
        new = max(0, current - 10)
        self.sp.volume(new, device_id=self.device_id)
        logger.info("Spotify volume set to %d%%", new)

    @safe_call
    def set_volume(self, level: int | str) -> None:
        try:
            v = max(0, min(100, int(level)))
        except (ValueError, TypeError):
            logger.error("Bad volume value: %r", level)
            return
        self.sp.volume(v, device_id=self.device_id)
        logger.info("Spotify volume set to %d%%", v)

    @safe_call
    def like(self) -> None:
        item = self.sp.current_user_playing_track().get("item")
        if not item:
            logger.error("No track playing to like")
            return
        self.sp.current_user_saved_tracks_add([item["id"]])
        logger.info("Spotify liked %s", item["id"])

    @safe_call
    def dislike(self) -> None:
        item = self.sp.current_user_playing_track().get("item")
        if not item:
            logger.error("No track playing to dislike")
            return
        self.sp.current_user_saved_tracks_delete([item["id"]])
        logger.info("Spotify disliked %s", item["id"])

    @safe_call
    def shuffle_on(self) -> None:
        self.sp.shuffle(True, device_id=self.device_id)
        logger.info("Spotify shuffle on")

    @safe_call
    def shuffle_off(self) -> None:
        self.sp.shuffle(False, device_id=self.device_id)
        logger.info("Spotify shuffle off")

    @safe_call
    def set_repeat(self, mode: str) -> None:
        if mode not in ("off", "context", "track"):
            logger.error("Invalid repeat mode: %r", mode)
            return
        self.sp.repeat(mode, device_id=self.device_id)
        logger.info("Spotify repeat set to %s", mode)

    @safe_call
    def queue_track(self, query: str) -> None:
        results = (
            self.sp.search(q=query, type="track", limit=1)
            .get("tracks", {})
            .get("items", [])
        )
        if not results:
            logger.error("Spotify queue: no results for %r", query)
            return
        uri = results[0]["uri"]
        self.sp.add_to_queue(uri, device_id=self.device_id)
        logger.info("Spotify queued %s", uri)

    @safe_call
    def current_track_info(self) -> dict:
        info = self.sp.current_user_playing_track() or {}
        logger.info("Spotify current playback info: %s", info)
        return info

    @safe_call
    def so_sad(self) -> None:
        sad_uri = "spotify:track:6rPO02ozF3bM7NnOV4h6s2"
        self.sp.start_playback(device_id=self.device_id, uris=[sad_uri])
        logger.info("Don't cry! %s", sad_uri)


# Lazy singleton
_client: SpotifyClient | None = None


def _get() -> SpotifyClient:
    global _client
    if _client is None:
        _client = SpotifyClient()
    return _client


def reset_client() -> None:
    global _client
    _client = None


# Action registrations

_END = r"[.!?\s]*$"
_W = r"\s*"

_BACKEND = "spotify"
_TRANSPORT = "oauth_cloud"
_SLOT = "music"
_PRECONDS = ("Spotify Connect device available", "OAuth credentials configured")


@action(
    name="spotify_play_music",
    capability="play_music",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Resume Spotify playback on the active Connect device.",
    patterns=[rf"^{_W}play\s+music{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state",),
    examples=("play music",),
)
def play_music() -> None:
    _get().play_music()


@action(
    name="spotify_stop_music",
    capability="stop_music",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Pause Spotify playback.",
    patterns=[rf"^{_W}stop\s+music{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state",),
    examples=("stop music",),
)
def stop_music() -> None:
    _get().stop_music()


@action(
    name="spotify_next_track",
    capability="next_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Skip to the next track in the Spotify queue.",
    patterns=[rf"^{_W}(?:next|skip){_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("current_track",),
    examples=("next", "skip"),
)
def next_track() -> None:
    _get().next_track()


@action(
    name="spotify_previous_track",
    capability="previous_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Go back to the previous Spotify track.",
    patterns=[rf"^{_W}(?:last|previous){_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("current_track",),
    examples=("last", "previous"),
)
def previous_track() -> None:
    _get().previous_track()


@action(
    name="spotify_restart_track",
    capability="restart_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Seek back to the start of the current Spotify track.",
    patterns=[rf"^{_W}restart{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("track_position",),
    examples=("restart",),
)
def restart_track() -> None:
    _get().restart_track()


@action(
    name="spotify_search_song",
    capability="search_song",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Search Spotify for a track and play the first match.",
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
    name="spotify_volume_up",
    capability="volume_up",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Step Spotify volume up by 10%.",
    patterns=[rf"^{_W}volume up{_END}"],
    preconditions=_PRECONDS,
    side_effects=("volume",),
    examples=("volume up",),
    no_early_match=True,
)
def volume_up() -> None:
    _get().volume_up()


@action(
    name="spotify_volume_down",
    capability="volume_down",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Step Spotify volume down by 10%.",
    patterns=[rf"^{_W}volume down{_END}"],
    preconditions=_PRECONDS,
    side_effects=("volume",),
    examples=("volume down",),
    no_early_match=True,
)
def volume_down() -> None:
    _get().volume_down()


@action(
    name="spotify_set_volume",
    capability="set_volume",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Set Spotify volume to a specific 0–100 level.",
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
    name="spotify_like",
    capability="like",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Save the current track to Your Library.",
    patterns=[rf"^{_W}like{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("library",),
    examples=("like",),
)
def like() -> None:
    _get().like()


@action(
    name="spotify_dislike",
    capability="dislike",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Remove the current track from Your Library.",
    patterns=[rf"^{_W}dislike{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("library",),
    examples=("dislike",),
)
def dislike() -> None:
    _get().dislike()


@action(
    name="spotify_shuffle_on",
    capability="shuffle_on",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Enable Spotify shuffle.",
    patterns=[rf"^{_W}shuffle\s+on{_END}"],
    preconditions=_PRECONDS,
    side_effects=("shuffle_state",),
    examples=("shuffle on",),
)
def shuffle_on() -> None:
    _get().shuffle_on()


@action(
    name="spotify_shuffle_off",
    capability="shuffle_off",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Disable Spotify shuffle.",
    patterns=[rf"^{_W}shuffle\s+off{_END}"],
    preconditions=_PRECONDS,
    side_effects=("shuffle_state",),
    examples=("shuffle off",),
)
def shuffle_off() -> None:
    _get().shuffle_off()


@action(
    name="spotify_set_repeat",
    capability="set_repeat",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Set Spotify repeat mode.",
    patterns=[rf"^{_W}repeat\s+(off|context|track){_END}"],
    args=(ArgSpec("mode", "enum:off|context|track", "Repeat mode."),),
    preconditions=_PRECONDS,
    side_effects=("repeat_state",),
    examples=("repeat off", "repeat track", "repeat context"),
)
def set_repeat(mode: str) -> None:
    _get().set_repeat(mode)


@action(
    name="spotify_queue_track",
    capability="queue_track",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Search Spotify and add the first match to the queue.",
    patterns=[rf"^{_W}next\s+track(?:\s*[,;:]\s*|\s+)(.+?){_END}"],
    args=(ArgSpec("query", "str", "Search query for the track to queue."),),
    preconditions=_PRECONDS,
    side_effects=("queue",),
    examples=("next track bohemian rhapsody",),
    no_early_match=True,
)
def queue_track(query: str) -> None:
    _get().queue_track(query)


@action(
    name="spotify_current_track_info",
    capability="current_track_info",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Log metadata for the currently playing Spotify track.",
    patterns=[rf"^{_W}(?:what(?:'s)?\s+playing|current\s+track\s+info|track\s+info){_END}"],
    preconditions=_PRECONDS,
    side_effects=(),
    examples=("what's playing", "track info"),
)
def current_track_info() -> None:
    _get().current_track_info()


@action(
    name="spotify_so_sad",
    capability="so_sad",
    backend=_BACKEND,
    slot=_SLOT,
    transport=_TRANSPORT,
    summary="Easter egg — play the 'so sad' track on Spotify.",
    patterns=[rf"^{_W}this\s+is\s+so\s+sad{_W}{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state", "current_track"),
    examples=("this is so sad",),
)
def so_sad() -> None:
    _get().so_sad()
