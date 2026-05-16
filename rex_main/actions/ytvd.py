"""YTVD (YouTube Video + Music Desktop fork) video-side actions.

Talks to YTVD's companion-server on the new namespaces:

* ``/api/v1/playback/*`` — source-agnostic (acts on whichever view owns
  the audio bus). Used here for seek-relative.
* ``/api/v1/video/*``    — video-specific (fullscreen, captions, theater,
  playback rate, search, navigate).

YTVD reuses YTMD's port, token, and env vars (``YTMD_HOST``,
``YTMD_PORT``, ``YTMD_TOKEN``), so authentication is shared. Music-side
commands continue to live in ``ytmd.py`` and hit the original
``/api/v1/command`` endpoint, which YTVD preserves unchanged.

See: docs/YTVD_COMPANION_API.md
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Optional, TypeVar

import requests

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


class YTVD:
    """Thin client for YTVD's /api/v1/playback and /api/v1/video routes."""

    def __init__(
        self,
        host: str | None = None,
        port: str | None = None,
        token: str | None = None,
        timeout: int = 5,
    ) -> None:
        raw_host = host or os.getenv("YTMD_HOST", "127.0.0.1")
        # Coerce "localhost" → "127.0.0.1" for the same IPv6-fallback reason
        # the YTMD client does; YTVD listens on the same socket.
        self.host = "127.0.0.1" if raw_host.lower() == "localhost" else raw_host
        self.port = port or os.getenv("YTMD_PORT", "9863")
        self.token = token or os.getenv("YTMD_TOKEN")
        self.timeout = timeout

        base = f"http://{self.host}:{self.port}/api/v1"
        self._playback_url = f"{base}/playback/command"
        self._video_url = f"{base}/video/command"
        self._headers = {"Content-Type": "application/json"}
        if self.token:
            self._headers["Authorization"] = self.token

        self._session = requests.Session()
        self._session.headers.update(self._headers)

    def _post(self, url: str, command: str, *, value: Optional[Any] = None) -> None:
        payload: dict[str, Any] = {"command": command}
        if value is not None:
            payload["data"] = value
        try:
            r = self._session.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error("YTVD command %r timed out after %ss", command, self.timeout)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "??"
            logger.error("YTVD command %r failed: HTTP %s", command, status)
        except requests.exceptions.RequestException as e:
            logger.error("YTVD command %r connection error: %s", command, e)
        else:
            logger.debug("YTVD: %s (%s) → %s", command, value, url)

    # /playback/* — source-agnostic

    def seek_relative(self, delta_seconds: int | str) -> None:
        try:
            delta = int(delta_seconds)
        except (ValueError, TypeError):
            logger.error("Bad seek delta: %s", delta_seconds)
            return
        self._post(self._playback_url, "seekRelative", value=delta)

    # /video/* — video-specific

    def toggle_fullscreen(self) -> None:
        self._post(self._video_url, "toggleFullscreen")

    def toggle_captions(self) -> None:
        self._post(self._video_url, "toggleCaptions")

    def toggle_theater(self) -> None:
        self._post(self._video_url, "toggleTheater")

    def set_playback_rate(self, rate: float | str) -> None:
        try:
            r = float(rate)
        except (ValueError, TypeError):
            logger.error("Bad playback rate: %s", rate)
            return
        # YTVD clamps server-side, but mirror the documented 0.25–2 range
        # so we never round-trip an obviously-bad request.
        r = max(0.25, min(2.0, r))
        self._post(self._video_url, "setPlaybackRate", value=r)

    def search(self, query: str) -> None:
        q = (query or "").strip()
        if not q:
            logger.error("Empty YouTube search query")
            return
        self._post(self._video_url, "search", value=q[:200])

    def navigate(self, dest: str) -> None:
        target = (dest or "").lower().strip()
        if target not in ("home", "subscriptions", "library"):
            logger.error("Bad navigate target: %s", dest)
            return
        self._post(self._video_url, "navigate", value=target)


# Lazy singleton — only instantiate when the user actually invokes an action.
_client: YTVD | None = None


def _get() -> YTVD:
    global _client
    if _client is None:
        _client = YTVD()
    return _client


def reset_client() -> None:
    """Drop the cached client so env-var changes take effect on next call."""
    global _client
    _client = None


# Wrap network-touching methods so transient errors don't kill the assistant.
for _name in (
    "seek_relative",
    "toggle_fullscreen",
    "toggle_captions",
    "toggle_theater",
    "set_playback_rate",
    "search",
    "navigate",
):
    setattr(YTVD, _name, safe_call(getattr(YTVD, _name)))


# Action registrations

_END = r"[.!?\s]*$"
_W = r"\s*"

_BACKEND = "ytvd"
_TRANSPORT = "http_local"
_PRECONDS = ("YTVD desktop app running with Companion-Server enabled",)


@action(
    name="ytvd_toggle_fullscreen",
    capability="toggle_fullscreen",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Toggle YouTube video fullscreen.",
    patterns=[rf"^{_W}(?:toggle\s+)?(?:fullscreen|full\s+screen){_END}"],
    preconditions=_PRECONDS,
    side_effects=("video_fullscreen",),
    examples=("fullscreen", "full screen", "toggle fullscreen"),
)
def toggle_fullscreen() -> None:
    _get().toggle_fullscreen()


@action(
    name="ytvd_toggle_captions",
    capability="toggle_captions",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Toggle YouTube video captions / subtitles.",
    patterns=[rf"^{_W}(?:toggle\s+)?(?:captions|subtitles){_END}"],
    preconditions=_PRECONDS,
    side_effects=("video_captions",),
    examples=("captions", "subtitles", "toggle captions"),
)
def toggle_captions() -> None:
    _get().toggle_captions()


@action(
    name="ytvd_toggle_theater",
    capability="toggle_theater",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Toggle YouTube video theater mode.",
    patterns=[rf"^{_W}(?:toggle\s+)?theater(?:\s+mode)?{_END}"],
    preconditions=_PRECONDS,
    side_effects=("video_theater",),
    examples=("theater", "theater mode", "toggle theater"),
)
def toggle_theater() -> None:
    _get().toggle_theater()


@action(
    name="ytvd_set_playback_rate",
    capability="set_playback_rate",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Set YouTube video playback speed (0.25–2.0).",
    patterns=[
        rf"^{_W}(?:set\s+)?(?:playback\s+)?speed\s+(?:to\s+)?(\d+(?:\.\d+)?){_END}"
    ],
    args=(ArgSpec("rate", "str", "Playback rate, 0.25–2.0 (clamped)."),),
    preconditions=_PRECONDS,
    side_effects=("video_playback_rate",),
    examples=("speed 1.5", "playback speed 2", "set speed to 1"),
    no_early_match=True,
)
def set_playback_rate(rate: str) -> None:
    _get().set_playback_rate(rate)


@action(
    name="ytvd_seek_forward",
    capability="seek_forward",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Skip forward N seconds in the active playback source.",
    patterns=[
        rf"^{_W}(?:skip\s+ahead|skip\s+forward|jump\s+ahead|jump\s+forward|fast\s+forward)\s+(\d+)(?:\s+seconds?)?{_END}"
    ],
    args=(ArgSpec("seconds", "int", "Seconds to skip forward."),),
    preconditions=_PRECONDS,
    side_effects=("track_position",),
    examples=("skip ahead 10", "jump forward 30", "fast forward 5"),
    no_early_match=True,
)
def seek_forward(seconds: int | str) -> None:
    try:
        n = int(seconds)
    except (ValueError, TypeError):
        logger.error("Bad seek-forward value: %s", seconds)
        return
    _get().seek_relative(abs(n))


@action(
    name="ytvd_seek_back",
    capability="seek_back",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Skip backward N seconds in the active playback source.",
    patterns=[
        rf"^{_W}(?:skip\s+back|skip\s+backward|jump\s+back|go\s+back|rewind)\s+(\d+)(?:\s+seconds?)?{_END}"
    ],
    args=(ArgSpec("seconds", "int", "Seconds to skip backward."),),
    preconditions=_PRECONDS,
    side_effects=("track_position",),
    examples=("skip back 10", "rewind 30", "jump back 5"),
    no_early_match=True,
)
def seek_back(seconds: int | str) -> None:
    try:
        n = int(seconds)
    except (ValueError, TypeError):
        logger.error("Bad seek-back value: %s", seconds)
        return
    _get().seek_relative(-abs(n))


@action(
    name="ytvd_search_youtube",
    capability="search_youtube",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Search YouTube on the video view.",
    patterns=[rf"^{_W}youtube\s+search\s+(.+?){_END}"],
    args=(ArgSpec("query", "str", "Search query."),),
    preconditions=_PRECONDS,
    side_effects=("video_url",),
    examples=("youtube search cat videos", "youtube search how to cook rice"),
    no_early_match=True,
)
def search_youtube(query: str) -> None:
    _get().search(query)


@action(
    name="ytvd_navigate",
    capability="navigate",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Navigate the YouTube video view to home / subscriptions / library.",
    patterns=[
        rf"^{_W}(?:go\s+to|show)\s+(home|subscriptions|library){_END}"
    ],
    args=(ArgSpec("dest", "enum:home|subscriptions|library", "Destination page."),),
    preconditions=_PRECONDS,
    side_effects=("video_url",),
    examples=("go to home", "go to subscriptions", "show library"),
)
def navigate(dest: str) -> None:
    _get().navigate(dest)
