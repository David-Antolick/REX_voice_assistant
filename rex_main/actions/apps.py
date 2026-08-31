"""Application launch / focus / close actions.

Voice commands to open, focus and close *any* installed app. Always-on
(no slot) — opening Spotify is meaningful even when the active music
backend is YTMD.

The spoken name is fuzzy-matched against Windows' own Start-menu
catalog, enumerated once per session. Fuzzy is not a nicety: Whisper
returns "blunder" for "blender" and "discored" for "discord" often
enough that exact matching feels broken.

Launch strategy is layered so install location doesn't matter:

1. Hardcoded candidate exe paths — fast, and the only tier that works
   for apps Windows doesn't list (see ``_SEEDED``).
2. Recursive search of the user's Start menu shortcuts (.lnk),
   resolved via PowerShell's WScript.Shell COM. Catches Squirrel /
   Electron / installer apps wherever they put themselves.
3. The catalog's AppUserModelID, launched through
   ``shell:AppsFolder\\<AppID>``. Catches Microsoft Store apps and
   anything else Windows recognizes.

Whichever tier succeeds, the result is cached for the session so a
single voice command never pays the discovery cost twice.

Focus and close work off a live enumeration of visible top-level
windows instead of the catalog — both need a window that exists *now*.
Close sends WM_CLOSE so the app can offer to save; nothing here
force-kills a process.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from rex_main.actions.registry import ArgSpec, action

logger = logging.getLogger(__name__)


# Catalog

@dataclass(frozen=True)
class _App:
    """One entry in the installed-app catalog."""
    name: str                              # display name, as Windows shows it
    key: str                               # normalized name, for fuzzy matching
    appid: str = ""                        # AppUserModelID, or a URI for Steam entries
    exe_paths: tuple[str, ...] = ()        # candidate absolute paths (first hit wins)
    lnk_names: tuple[str, ...] = ()        # Start-menu .lnk filenames, without extension
    process_names: tuple[str, ...] = ()    # image names to match windows against


# Apps REX carries paths for rather than trusting the catalog. Both are
# music backends whose commands must work on a fresh install, and neither
# registers with Get-StartApps on this machine — YTMD/Spotify install
# per-user outside the Start-menu app list.
_SEEDED: tuple[_App, ...] = (
    _App(
        name="YouTube Music",
        key="youtube music",
        exe_paths=(
            r"%LOCALAPPDATA%\Programs\youtube-music\YouTube Music.exe",
            r"%PROGRAMFILES%\YouTube Music\YouTube Music.exe",
            r"%PROGRAMFILES(X86)%\YouTube Music\YouTube Music.exe",
        ),
        lnk_names=("YouTube Music", "youtube-music"),
        process_names=("YouTube Music.exe", "youtube-music.exe"),
    ),
    _App(
        name="Spotify",
        key="spotify",
        exe_paths=(
            r"%APPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
            r"%PROGRAMFILES%\WindowsApps\SpotifyAB.SpotifyMusic_*\Spotify.exe",
        ),
        lnk_names=("Spotify",),
        process_names=("Spotify.exe",),
    ),
)

# Get-StartApps returns help files, release notes, uninstallers and vendor
# web links alongside real apps. Left in, they triple the fuzzy-match
# space with entries nobody would ever ask REX to open.
_NOISE_TOKENS = frozenset(
    {"uninstall", "readme", "manuals", "docs", "help", "website", "faqs"}
)
_NOISE_TARGETS = (".chm", ".html", ".htm", ".txt", ".pdf", ".url")

_catalog: Optional[list[_App]] = None
_catalog_lock = threading.Lock()
_warm_thread: Optional[threading.Thread] = None

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Lowercase, punctuation-free, single-spaced form used for matching."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _start_apps() -> list[tuple[str, str]]:
    """Ask Windows for its full installed-app catalog as (name, appid)."""
    cmd = "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            text=True,
            timeout=30,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        parsed = json.loads(out or "[]")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enumerate installed apps: %s", exc)
        return []

    if isinstance(parsed, dict):  # PowerShell emits a bare object for one result
        parsed = [parsed]
    return [
        (e["Name"], e["AppID"])
        for e in parsed
        if isinstance(e, dict) and e.get("Name") and e.get("AppID")
    ]


def _is_launchable(name: str, appid: str) -> bool:
    lowered = appid.lower()
    if lowered.startswith(("http://", "https://")) or lowered.endswith(_NOISE_TARGETS):
        return False
    return not (_NOISE_TOKENS & set(_norm(name).split()))


def _build_catalog() -> list[_App]:
    by_key = {app.key: app for app in _SEEDED}
    for name, appid in _start_apps():
        if not _is_launchable(name, appid):
            continue
        key = _norm(name)
        if not key:
            continue
        seeded = by_key.get(key)
        if seeded is not None:
            by_key[key] = replace(seeded, appid=seeded.appid or appid)
            continue
        by_key[key] = _App(name=name, key=key, appid=appid, lnk_names=(name,))

    catalog = sorted(by_key.values(), key=lambda a: a.name.lower())
    logger.info("App catalog: %d entries", len(catalog))
    return catalog


def _catalog_entries() -> list[_App]:
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _catalog = _build_catalog()
        return _catalog


def warm() -> None:
    """Build the app catalog off the dispatch path, at startup.

    Enumerating Get-StartApps costs a PowerShell round-trip of roughly a
    second. Doing it lazily would hand that latency to the user's first
    "open X" — the one command where they are already waiting.
    """
    global _warm_thread
    with _catalog_lock:
        if _catalog is not None or (_warm_thread is not None and _warm_thread.is_alive()):
            return
        _warm_thread = threading.Thread(
            target=_catalog_entries, name="rex-app-catalog", daemon=True
        )
        _warm_thread.start()


# Fuzzy matching

_MATCH_MIN = 0.72          # below this, a spoken name is treated as unknown
_PREFIX_MIN_CHARS = 4      # "d" must not prefix-match "discord"


def _similarity(spoken: str, candidate: str) -> float:
    """0..1 score between two normalized names."""
    if not spoken or not candidate:
        return 0.0
    if spoken == candidate:
        return 1.0
    a, b = spoken.replace(" ", ""), candidate.replace(" ", "")
    if a == b:
        return 0.98
    if len(a) >= _PREFIX_MIN_CHARS and (b.startswith(a) or a.startswith(b)):
        return 0.95        # "blender" vs "blender 4 2"
    if f" {spoken} " in f" {candidate} ":
        return 0.90        # whole word inside a longer title
    return SequenceMatcher(None, a, b).ratio()


def _match_app(spoken: str) -> Optional[_App]:
    """Best catalog entry for a spoken name, or None if nothing is close."""
    key = _norm(spoken)
    if not key:
        return None
    best: Optional[_App] = None
    best_score = 0.0
    for app in _catalog_entries():
        score = _similarity(key, app.key)
        if score > best_score:
            best, best_score = app, score
    if best is None or best_score < _MATCH_MIN:
        logger.warning("No installed app matches %r", spoken)
        return None
    logger.info("Resolved %r to %s (%.2f)", spoken, best.name, best_score)
    return best


# Launch resolution

# Per-process cache of the launch handle for each app, populated on first
# successful resolution. Each entry is one of:
#   ("exe",   "C:\\full\\path\\to.exe")   -> spawn directly
#   ("appid", "Name_publisher!AppId")     -> launch via shell:AppsFolder
#   ("uri",   "steam://rungameid/1234")   -> hand to the shell as-is
_LAUNCH_CACHE: dict[str, tuple[str, str]] = {}

_START_MENU_DIRS = (
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs",
    r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs",
)


def _resolve_exe_paths(app: _App) -> Optional[Path]:
    """Tier 1: try the hardcoded exe candidates."""
    for raw in app.exe_paths:
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


def _resolve_via_start_menu(app: _App) -> Optional[Path]:
    """Tier 2: scan the Start menu for matching .lnk files and resolve the
    target via PowerShell's WScript.Shell COM object."""
    candidates: list[Path] = []
    for raw in _START_MENU_DIRS:
        root = Path(os.path.expandvars(raw))
        if not root.is_dir():
            continue
        for name in app.lnk_names:
            candidates.extend(root.rglob(f"{name}.lnk"))

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


def _resolve(app: _App) -> Optional[tuple[str, str]]:
    """Return a launch handle for ``app``, cached after the first hit."""
    cached = _LAUNCH_CACHE.get(app.key)
    if cached is not None:
        return cached

    exe = _resolve_exe_paths(app)
    if exe is not None:
        logger.info("Resolved %s via known path: %s", app.name, exe)
        _LAUNCH_CACHE[app.key] = ("exe", str(exe))
        return _LAUNCH_CACHE[app.key]

    exe = _resolve_via_start_menu(app)
    if exe is not None:
        logger.info("Resolved %s via Start menu shortcut: %s", app.name, exe)
        _LAUNCH_CACHE[app.key] = ("exe", str(exe))
        return _LAUNCH_CACHE[app.key]

    if app.appid:
        kind = "uri" if "://" in app.appid else "appid"
        logger.info("Resolved %s via app catalog: %s", app.name, app.appid)
        _LAUNCH_CACHE[app.key] = (kind, app.appid)
        return _LAUNCH_CACHE[app.key]

    return None


# Window enumeration

@dataclass(frozen=True)
class _Win:
    hwnd: int
    title: str
    pid: int
    image: str          # exe basename, e.g. "Discord.exe"


_SW_RESTORE = 9
_WM_CLOSE = 0x0010
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _image_name(kernel32, pid: int) -> str:
    from ctypes import wintypes

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _visible_windows() -> list[_Win]:
    """Visible, titled, top-level windows that aren't REX's own.

    Deliberately uncached — focus and close need the desktop as it is at
    the moment the command is spoken.
    """
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    own_pid = os.getpid()
    found: list[_Win] = []

    def _collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != own_pid:
            found.append(
                _Win(hwnd, buf.value, pid.value, _image_name(kernel32, pid.value))
            )
        return True

    try:
        user32.EnumWindows(enum_proc(_collect), 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Window enumeration failed: %s", exc)
    return found


def _find_windows(spoken: str, app: Optional[_App]) -> list[_Win]:
    """Windows belonging to the app the user named, best process first.

    Matches on image name, then window title, against both what was said
    and the catalog name it resolved to — "switch to brave" has to find a
    window titled "some-github-page - Brave".
    """
    names = [n for n in (_norm(spoken), app.key if app else "") if n]
    if not names:
        return []
    process_names = {p.lower() for p in app.process_names} if app else set()

    scored: list[tuple[float, _Win]] = []
    for win in _visible_windows():
        if win.image.lower() in process_names:
            score = 1.0
        else:
            image_key = _norm(Path(win.image).stem)
            title_key = _norm(win.title)
            score = max(
                max(_similarity(n, image_key), _similarity(n, title_key))
                for n in names
            )
        if score >= _MATCH_MIN:
            scored.append((score, win))

    if not scored:
        return []
    scored.sort(key=lambda s: -s[0])
    top_pid = scored[0][1].pid
    return [win for _, win in scored if win.pid == top_pid]


def _raise_window(hwnd: int) -> bool:
    """Bring a window to the foreground.

    Windows refuses SetForegroundWindow from a process that isn't already
    the foreground one — which REX, sitting in the tray, never is. The
    plain call measured 2/5 from a cold background process; attaching our
    input queue to both the outgoing-foreground and target threads first
    measured 5/5. See docs/DECISIONS.md.
    """
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    # HWNDs are pointer-sized; without argtypes ctypes would truncate them to int.
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True

    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    own_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(own_tid, foreground_tid, True)
    user32.AttachThreadInput(own_tid, target_tid, True)
    try:
        user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(own_tid, target_tid, False)
        user32.AttachThreadInput(own_tid, foreground_tid, False)
    return user32.GetForegroundWindow() == hwnd


# Open / focus / close

def _open_by_name(spoken: str) -> None:
    app = _match_app(spoken)
    if app is None:
        return

    handle = _resolve(app)
    if handle is None:
        logger.warning(
            "Could not locate %s — exe paths, Start menu, and the app catalog "
            "all came up empty. Open it once manually so Windows registers it, "
            "or check that it is installed.",
            app.name,
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
        else:
            # explorer.exe handles both the AppsFolder scheme and protocol URIs.
            target = value if kind == "uri" else f"shell:AppsFolder\\{value}"
            subprocess.Popen(["explorer.exe", target], close_fds=True)
        logger.info("Launched %s", app.name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to launch %s: %s", app.name, exc)
        _LAUNCH_CACHE.pop(app.key, None)   # re-resolve on the next attempt


def _focus_by_name(spoken: str) -> None:
    app = _match_app(spoken)
    windows = _find_windows(spoken, app)
    if not windows:
        logger.warning("No open window for %r", app.name if app else spoken)
        return
    target = windows[0]
    if _raise_window(target.hwnd):
        logger.info("Focused %s (%s)", target.title, target.image)
    else:
        logger.warning("Windows refused the foreground change for %s", target.image)


def _close_by_name(spoken: str) -> None:
    """Ask an app to close.

    WM_CLOSE, never taskkill /F: generalizing from two known music apps to
    anything installed means "close X" can now land on an editor holding
    unsaved work, and the app must get its chance to say so.
    """
    app = _match_app(spoken)
    windows = _find_windows(spoken, app)
    if not windows:
        logger.warning("No open window for %r", app.name if app else spoken)
        return

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    for win in windows:
        try:
            user32.PostMessageW(win.hwnd, _WM_CLOSE, 0, 0)
            logger.info("Sent close request to %s (%s)", win.title, win.image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Close request failed for %s: %s", win.image, exc)


# Phrase fragments

_END = r"[.!?\s]*$"
_W = r"\s*"
_OPEN = r"(?:open|launch|start)"
_CLOSE = r"(?:close|quit|exit|kill)"
_NAME = r"([A-Za-z][\w .'&+-]{0,39}?)"

# Phrases other backends own. Without these guards the app-name capture
# swallows them and the winner is decided by module import order — the
# exact failure docs/PC_CONTROL_PLAN.md calls out.
#   window / desktop  -> system_window
#   home / subscriptions / library -> ytvd navigate
#   spotify / youtube music (after "switch to") -> rex backend switching
_NOT_WINDOW = r"(?!window\b|desktop\b)"
_NOT_SWITCHABLE = (
    r"(?!window\b|desktop\b|home\b|subscriptions\b|library\b"
    r"|spotify\b|youtube\s+music\b)"
)

_BACKEND = "apps"
_TRANSPORT = "os_native"


@action(
    name="apps_open_app",
    capability="open_app",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Launch an installed app by name.",
    patterns=[rf"^{_W}{_OPEN}\s+{_NOT_WINDOW}{_NAME}{_END}"],
    args=(ArgSpec("name", "str", "Spoken app name, fuzzy-matched to the catalog."),),
    side_effects=("app_process_running",),
    examples=("open spotify", "open youtube music", "launch discord", "start blender"),
    no_early_match=True,
)
def open_app(name: str) -> None:
    _open_by_name(name)


@action(
    name="apps_focus_app",
    capability="focus_app",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Bring an already-running app's window to the foreground.",
    patterns=[
        rf"^{_W}(?:switch\s+to|go\s+to)\s+{_NOT_SWITCHABLE}{_NAME}{_END}",
        rf"^{_W}focus\s+{_NOT_WINDOW}{_NAME}{_END}",
    ],
    args=(ArgSpec("name", "str", "Spoken app name, fuzzy-matched to open windows."),),
    preconditions=("The app already has a visible window",),
    side_effects=("foreground_window",),
    examples=("switch to discord", "go to chrome", "focus spotify"),
    no_early_match=True,
)
def focus_app(name: str) -> None:
    _focus_by_name(name)


@action(
    name="apps_close_app",
    capability="close_app",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Ask an app to close, so it can prompt to save.",
    patterns=[rf"^{_W}{_CLOSE}\s+{_NOT_WINDOW}{_NAME}{_END}"],
    args=(ArgSpec("name", "str", "Spoken app name, fuzzy-matched to open windows."),),
    preconditions=("The app already has a visible window",),
    side_effects=("app_process_running",),
    examples=("close spotify", "close youtube music", "quit discord", "exit blender"),
    no_early_match=True,
)
def close_app(name: str) -> None:
    _close_by_name(name)
