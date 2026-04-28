"""Service-switching actions and config-driven backend wiring.

`configure_from_config` is the entry point used by the CLI / runtime: it
reads the user's config, exports any required env vars (so the lazy
clients in actions/ytmd.py and actions/spotify.py pick them up on first
call), and tells the registry which backend owns the "music" slot.

`switch_to_spotify` and `switch_to_ytmd` are voice-triggered actions that
flip the active music backend at runtime.
"""

from __future__ import annotations

import logging
import os

from rex_main.actions import discord as _discord_module
from rex_main.actions import ytmd as _ytmd_module
from rex_main.actions import spotify as _spotify_module
from rex_main.actions.registry import action, set_active_backend, set_active_backends

logger = logging.getLogger(__name__)


# Config-driven configuration

def configure_from_config(config: dict) -> None:
    """Activate backends and export their secrets/env-vars from `config`."""
    from rex_main.config import get_secrets

    secrets = get_secrets(config)
    services = config.get("services", {}) or {}
    active = (services.get("active") or "none").lower()

    if active == "ytmd":
        ytmd_cfg = services.get("ytmd", {}) or {}
        if secrets.get("ytmd_token"):
            os.environ["YTMD_TOKEN"] = secrets["ytmd_token"]
        if ytmd_cfg.get("host"):
            os.environ["YTMD_HOST"] = ytmd_cfg["host"]
        if ytmd_cfg.get("port"):
            os.environ["YTMD_PORT"] = str(ytmd_cfg["port"])

        _ytmd_module.reset_client()
        set_active_backends({"music": "ytmd"})
        _warm_client(_ytmd_module._get, "ytmd")
        logger.info("Active music backend: ytmd")

    elif active == "spotify":
        sp_cfg = services.get("spotify", {}) or {}
        if secrets.get("spotify_client_id"):
            os.environ["SPOTIPY_CLIENT_ID"] = secrets["spotify_client_id"]
        if secrets.get("spotify_client_secret"):
            os.environ["SPOTIPY_CLIENT_SECRET"] = secrets["spotify_client_secret"]
        if sp_cfg.get("redirect_uri"):
            os.environ["SPOTIPY_REDIRECT_URI"] = sp_cfg["redirect_uri"]

        _spotify_module.reset_client()
        set_active_backends({"music": "spotify"})
        _warm_client(_spotify_module._get, "spotify")
        logger.info("Active music backend: spotify")

    else:
        set_active_backends({"music": None})
        logger.warning("No music backend active — running in transcription-only mode")

    # Always-on integrations: pre-warm so the first voice command is fast.
    _discord_module.warm()


def _warm_client(getter, name: str) -> None:
    """Eagerly instantiate the active backend client so the first voice
    command doesn't pay OAuth / device-discovery round-trips in the
    dispatch path. Failures here are non-fatal — the lazy path will
    retry on first invocation."""
    try:
        getter()
    except Exception as e:
        logger.warning("Could not pre-warm %s client: %s", name, e)


# Action registrations (always-on; no slot)

_END = r"[.!?\s]*$"
_W = r"\s*"


@action(
    name="switch_to_spotify",
    capability="switch_music_backend",
    backend="rex",
    slot=None,
    transport="local",
    summary="Switch the active music backend to Spotify.",
    patterns=[rf"^{_W}switch\s+to\s+spotify{_END}"],
    side_effects=("active_music_backend",),
    examples=("switch to spotify",),
)
def switch_to_spotify() -> None:
    _spotify_module.reset_client()
    set_active_backend("music", "spotify")
    logger.info("Switched music backend to Spotify")


@action(
    name="switch_to_ytmd",
    capability="switch_music_backend",
    backend="rex",
    slot=None,
    transport="local",
    summary="Switch the active music backend to YouTube Music Desktop.",
    patterns=[rf"^{_W}switch\s+to\s+youtube\s+music{_END}"],
    side_effects=("active_music_backend",),
    examples=("switch to youtube music",),
)
def switch_to_ytmd() -> None:
    _ytmd_module.reset_client()
    set_active_backend("music", "ytmd")
    logger.info("Switched music backend to YTMD")
