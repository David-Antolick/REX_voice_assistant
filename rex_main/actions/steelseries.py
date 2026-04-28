"""SteelSeries GG Moments actions.

Triggers clip saves via the GameSense SDK against the local SteelSeries GG
server. See: https://github.com/SteelSeries/gamesense-sdk/blob/master/doc/api/sending-moments-events.md
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from rex_main.actions.registry import action

logger = logging.getLogger(__name__)

_requests = None


def _get_requests():
    global _requests
    if _requests is None:
        import requests
        _requests = requests
    return _requests


def _get_gamesense_address() -> Optional[str]:
    """Read the GameSense server address from coreProps.json."""
    paths = [
        os.path.expandvars(r"%PROGRAMDATA%\SteelSeries\SteelSeries Engine 3\coreProps.json"),
        os.path.expandvars(r"%PROGRAMDATA%\SteelSeries\GG\coreProps.json"),
    ]
    for path in paths:
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if "address" in data:
                    return data["address"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            continue
    return None


class SteelSeriesMoments:
    """Client for SteelSeries GG Moments autoclipping."""

    GAME_NAME = "REX"
    GAME_DISPLAY_NAME = "REX Voice Assistant"
    CLIP_RULE_KEY = "voice_clip"

    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self._base_url: Optional[str] = None
        self._registered = False
        self._session = None  # lazy — created with the requests module on first use

    def _get_base_url(self) -> Optional[str]:
        if self._base_url is None:
            address = _get_gamesense_address()
            if address:
                self._base_url = f"http://{address}"
                logger.debug("SteelSeries GameSense server: %s", self._base_url)
            else:
                logger.warning("SteelSeries GG not found. Is it running?")
        return self._base_url

    def _post(self, endpoint: str, data: dict) -> bool:
        base = self._get_base_url()
        if not base:
            return False

        requests = _get_requests()
        if self._session is None:
            self._session = requests.Session()
        try:
            r = self._session.post(f"{base}/{endpoint}", json=data, timeout=self.timeout)
            r.raise_for_status()
            logger.debug("SteelSeries %s: %s", endpoint, r.text[:200])
            return True
        except requests.exceptions.Timeout:
            logger.error("SteelSeries %s timed out", endpoint)
            return False
        except requests.exceptions.RequestException as e:
            logger.error("SteelSeries %s failed: %s", endpoint, e)
            return False

    def register(self) -> bool:
        """Register REX with GameSense and set up autoclip rules. Idempotent."""
        if self._registered:
            return True

        metadata = {
            "game": self.GAME_NAME,
            "game_display_name": self.GAME_DISPLAY_NAME,
            "developer": "REX",
        }
        if not self._post("game_metadata", metadata):
            return False

        rules = {
            "game": self.GAME_NAME,
            "rules": [
                {
                    "rule_key": self.CLIP_RULE_KEY,
                    "label": "Voice Command Clip",
                    "default_enabled": True,
                }
            ],
        }
        if not self._post("register_autoclip_rules", rules):
            return False

        self._registered = True
        logger.info("SteelSeries Moments: REX registered for clipping")
        return True

    def clip(self) -> bool:
        if not self._registered:
            self.register()
        trigger = {"game": self.GAME_NAME, "key": self.CLIP_RULE_KEY}
        success = self._post("autoclip", trigger)
        if success:
            logger.info("SteelSeries Moments: Clip triggered")
        return success


# Lazy singleton
_moments: Optional[SteelSeriesMoments] = None


def _get() -> SteelSeriesMoments:
    global _moments
    if _moments is None:
        _moments = SteelSeriesMoments()
    return _moments


# Action registrations

_END = r"[.!?\s]*$"
_W = r"\s*"


@action(
    name="steelseries_clip_that",
    capability="clip_that",
    backend="steelseries",
    slot=None,  # always-on; only one clipping backend at the moment
    transport="gamesense",
    summary="Save the last several seconds of gameplay as a SteelSeries Moments clip.",
    patterns=[
        # "capture" / "record" use harder consonants for better recognition.
        rf"^{_W}(?:clip\s+(?:that|it)|save\s+(?:that|clip)|capture\s+(?:that|it)|record\s+(?:that|clip)){_END}",
    ],
    preconditions=(
        "SteelSeries GG running",
        "Moments enabled and recording",
        "REX autoclipping enabled in GG > Settings > Moments > Apps",
    ),
    side_effects=("clip_saved",),
    examples=("clip that", "save that", "capture that", "record that"),
)
def clip_that() -> None:
    _get().clip()
