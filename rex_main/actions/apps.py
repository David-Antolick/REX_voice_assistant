"""Application launch / close actions.

Voice commands to open and close common companion apps (YouTube Music
Desktop, Spotify). Always-on (no slot) — opening Spotify is meaningful
even when the active music backend is YTMD.

Launch strategy is layered so install location doesn't matter:

1. Hardcoded candidate exe paths — fast, works for default installs.
2. Recursive search of the user's Start menu shortcuts (.lnk),
   resolved via PowerShell's WScript.Shell COM. Catches Squirrel /
   Electron / installer apps wherever they put themselves.
3. Windows' installed-apps catalog via PowerShell ``Get-StartApps``,
   launched through ``shell:AppsFolder\\<AppID>``. Catches Microsoft
   Store apps and anything else Windows recognizes.

Whichever step succeeds, the result is cached for the session so a
single voice command never pays the discovery cost twice.

Close uses ``taskkill /F /T`` against a list of candidate image names.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from rex_main.actions.registry import action

logger = logging.getLogger(__name__)


# App definitions

# - exe_paths:    candidate absolute paths (first hit wins)
# - lnk_names:    Start-menu .lnk filenames (without extension) to look for
# - start_query:  case-insensitive substring matched against Get-StartApps
# - process_names: image names taskkill should target on close
_APPS: dict[str, dict] = {
    "ytmd": {
        "display": "YouTube Music",
        "exe_paths": [
            r"%LOCALAPPDATA%\Programs\youtube-music\YouTube Music.exe",
            r"%PROGRAMFILES%\YouTube Music\YouTube Music.exe",
            r"%PROGRAMFILES(X86)%\YouTube Music\YouTube Music.exe",
        ],
        "lnk_names": ["YouTube Music", "youtube-music"],
        "start_query": "YouTube Music",
        "process_names": ["YouTube Music.exe", "youtube-music.exe"],
    },
    "spotify": {
        "display": "Spotify",
        "exe_paths": [
            r"%APPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
            r"%PROGRAMFILES%\WindowsApps\SpotifyAB.SpotifyMusic_*\Spotify.exe",
        ],
        "lnk_names": ["Spotify"],
        "start_query": "Spotify",
        "process_names": ["Spotify.exe"],
    },
}


# Resolution

# Per-process cache of the launch handle for each app — populated on first
# successful resolution. Each entry is one of:
#   ("exe",    "C:\\full\\path\\to.exe")     -> spawn directly
#   ("appid",  "Name_publisher!AppId")        -> launch via shell:AppsFolder
_LAUNCH_CACHE: dict[str, tuple[str, str]] = {}


_START_MENU_DIRS = [
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs",
    r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs",
]


def _resolve_exe_paths(spec: dict) -> Optional[Path]:
    """Step 1: try the hardcoded exe candidates."""
    for raw in spec["exe_paths"]:
        expanded = os.path.expandvars(raw)
        if "*" in expanded:
            parent = Path(expanded).parent.parent
            pattern = Path(expanded).parent.name
            if parent.is_dir():
                for sub in parent.glob(pattern):
                    candidate = sub / Path(expanded).name
                    if candidate.is_file():
                        return candidate
            continue
        p = Path(expanded)
        if p.is_file():
            return p
    return None


def _resolve_via_start_menu(spec: dict) -> Optional[Path]:
    """Step 2: scan the Start menu for matching .lnk files and resolve the
    target via PowerShell's WScript.Shell COM object."""
    candidates: list[Path] = []
    for raw in _START_MENU_DIRS:
        root = Path(os.path.expandvars(raw))
        if not root.is_dir():
            continue
        for name in spec["lnk_names"]:
            candidates.extend(root.rglob(f"{name}.lnk"))
    if not candidates:
        return None

    for lnk in candidates:
        target = _resolve_lnk_target(lnk)
        if target and Path(target).is_file():
            return Path(target)
    return None


def _resolve_lnk_target(lnk_path: Path) -> Optional[str]:
    """Return the TargetPath of a Windows .lnk shortcut, or None."""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(New-Object -COM WScript.Shell).CreateShortcut('{lnk_path}').TargetPath",
            ],
            text=True,
            timeout=6,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = out.strip()
        return result or None
    except Exception:
        return None


def _resolve_via_start_apps(spec: dict) -> Optional[str]:
    """Step 3: ask Windows for the AppUserModelID of an installed app whose
    Start-menu name contains ``spec['start_query']``. Returns the AppID
    suitable for ``shell:AppsFolder\\<AppID>`` launching, or None."""
    query = spec.get("start_query")
    if not query:
        return None
    try:
        # Single-quote the query for PowerShell. Escape any embedded single
        # quotes by doubling them.
        safe = query.replace("'", "''")
        cmd = (
            f"Get-StartApps | Where-Object {{ $_.Name -like '*{safe}*' }} "
            f"| Select-Object -First 1 -ExpandProperty AppID"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = out.strip()
        return result or None
    except Exception:
        return None


def _resolve(app_key: str) -> Optional[tuple[str, str]]:
    """Return a launch handle for ``app_key`` or None.

    Cached after the first successful lookup.
    """
    if app_key in _LAUNCH_CACHE:
        return _LAUNCH_CACHE[app_key]
    spec = _APPS.get(app_key)
    if spec is None:
        return None

    exe = _resolve_exe_paths(spec)
    if exe is not None:
        logger.info("Resolved %s via known path: %s", spec["display"], exe)
        _LAUNCH_CACHE[app_key] = ("exe", str(exe))
        return _LAUNCH_CACHE[app_key]

    exe = _resolve_via_start_menu(spec)
    if exe is not None:
        logger.info("Resolved %s via Start menu shortcut: %s", spec["display"], exe)
        _LAUNCH_CACHE[app_key] = ("exe", str(exe))
        return _LAUNCH_CACHE[app_key]

    appid = _resolve_via_start_apps(spec)
    if appid:
        logger.info("Resolved %s via Get-StartApps: %s", spec["display"], appid)
        _LAUNCH_CACHE[app_key] = ("appid", appid)
        return _LAUNCH_CACHE[app_key]

    return None


# Open / close


def _open_app(app_key: str) -> None:
    spec = _APPS.get(app_key)
    if spec is None:
        logger.warning("open: unknown app %r", app_key)
        return

    handle = _resolve(app_key)
    if handle is None:
        logger.warning(
            "Could not locate %s — exe paths, Start menu, and Get-StartApps "
            "all came up empty. Open it once manually so Windows registers it, "
            "or check that it is installed.",
            spec["display"],
        )
        return

    kind, value = handle
    try:
        if kind == "exe":
            subprocess.Popen(
                [value],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:  # appid — explorer.exe handles the AppsFolder scheme.
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{value}"],
                close_fds=True,
            )
        logger.info("Launched %s", spec["display"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to launch %s: %s", spec["display"], exc)
        # Drop the cache so the next attempt re-resolves.
        _LAUNCH_CACHE.pop(app_key, None)


def _close_app(app_key: str) -> None:
    spec = _APPS.get(app_key)
    if spec is None:
        logger.warning("close: unknown app %r", app_key)
        return
    for image in spec["process_names"]:
        try:
            subprocess.run(
                ["taskkill", "/IM", image, "/F", "/T"],
                capture_output=True,
                check=False,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            logger.info("Sent taskkill to %s", image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("taskkill failed for %s: %s", image, exc)


# Phrase fragments

_END = r"[.!?\s]*$"
_W = r"\s*"
_OPEN = r"(?:open|launch|start)"
_CLOSE = r"(?:close|quit|exit|kill)"


# YouTube Music


@action(
    name="apps_open_youtube_music",
    capability="open_app",
    backend="apps",
    slot=None,
    transport="os_native",
    summary="Launch the YouTube Music Desktop app.",
    patterns=[rf"^{_W}{_OPEN}\s+youtube\s+music{_END}"],
    side_effects=("ytmd_process_running",),
    examples=("open youtube music", "launch youtube music", "start youtube music"),
)
def open_youtube_music() -> None:
    _open_app("ytmd")


@action(
    name="apps_close_youtube_music",
    capability="close_app",
    backend="apps",
    slot=None,
    transport="os_native",
    summary="Force-close the YouTube Music Desktop app.",
    patterns=[rf"^{_W}{_CLOSE}\s+youtube\s+music{_END}"],
    side_effects=("ytmd_process_running",),
    examples=("close youtube music", "quit youtube music", "exit youtube music"),
)
def close_youtube_music() -> None:
    _close_app("ytmd")


# Spotify


@action(
    name="apps_open_spotify",
    capability="open_app",
    backend="apps",
    slot=None,
    transport="os_native",
    summary="Launch the Spotify desktop app.",
    patterns=[rf"^{_W}{_OPEN}\s+spotify{_END}"],
    side_effects=("spotify_process_running",),
    examples=("open spotify", "launch spotify", "start spotify"),
)
def open_spotify() -> None:
    _open_app("spotify")


@action(
    name="apps_close_spotify",
    capability="close_app",
    backend="apps",
    slot=None,
    transport="os_native",
    summary="Force-close the Spotify desktop app.",
    patterns=[rf"^{_W}{_CLOSE}\s+spotify{_END}"],
    side_effects=("spotify_process_running",),
    examples=("close spotify", "quit spotify", "exit spotify"),
)
def close_spotify() -> None:
    _close_app("spotify")
