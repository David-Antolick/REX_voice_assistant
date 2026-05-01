"""Back-compat shim for the pre-registry layout.

The real action surface now lives in `rex_main.actions` — every voice
command is declared there with @action and dispatched by registry name.

This module re-exports a small surface so older callers (CLI bootstrap,
CI smoke tests, third-party imports) keep working. New code should
import from `rex_main.actions` directly.
"""

from __future__ import annotations

import logging

from rex_main.actions.registry import set_active_backends
from rex_main.actions.service import configure_from_config
from rex_main.actions.spotify import SpotifyClient
from rex_main.actions.ytmd import YTMD, safe_call

logger = logging.getLogger(__name__)

__all__ = [
    "YTMD",
    "SpotifyClient",
    "safe_call",
    "configure_from_config",
    "configure_service",
]


def configure_service(mode: str) -> None:
    """Legacy entry point — set the active music backend by string name."""
    mode = (mode or "none").lower()
    if mode in ("ytmd", "spotify"):
        set_active_backends({"music": mode})
    elif mode == "none":
        set_active_backends({"music": None})
    else:
        raise ValueError(f"Unknown service mode: {mode!r}")
    logger.info("Configured music backend: %s", mode)
