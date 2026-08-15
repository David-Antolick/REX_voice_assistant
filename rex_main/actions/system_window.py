"""Window and session control via ``user32``.

Always-on (slot=None) — there is one desktop, so nothing competes for
this surface. No new dependency: ``ctypes`` against ``user32`` the same
way ``rex_main/ui/hud.py`` binds it for click-through.

Two mechanisms, picked per command:

* **Direct window calls** (``ShowWindow`` / ``PostMessageW``) for things
  that target one window. Deterministic — they name the HWND rather
  than hoping the shell routes a keystroke to it.
* **Synthetic hotkeys** (``SendInput``) for Snap, Show Desktop and
  virtual-desktop switching. These are shell behaviours with no public
  API; Win+Left / Win+D / Win+Ctrl+Arrow *is* the interface.

Safety (docs/PC_CONTROL_PLAN.md, "Safety rails"):

* "close window" posts ``WM_CLOSE``, which the app may refuse or answer
  with a save prompt. ``apps.py`` uses ``taskkill /F`` because it is
  scoped to a named app; generic window close must never do that.
* Shutdown, restart, sleep and log off are deliberately absent. The
  cost of a misfire is unbounded and the convenience saved is a
  keystroke.
"""

from __future__ import annotations

import ctypes
import functools
import logging
from ctypes import wintypes
from typing import Any, Callable, Optional, TypeVar

from rex_main.actions.registry import action

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def safe_call(func: F) -> F:
    """Swallow user32 failures so one bad call can't kill the assistant.

    The music backends' ``safe_call`` guards network-shaped errors. Nothing
    here touches the network, but a vanished HWND or a foreground window
    owned by an elevated process (UIPI blocks our input) fails just as
    routinely, and the matcher's error boundary is the audio loop.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except OSError as e:
            logger.error("user32 call failed in %s: %s", func.__name__, e)
        except Exception as e:
            logger.exception("Unexpected error in %s: %s", func.__name__, e)
        return None
    return wrapper  # type: ignore[return-value]


# user32 constants

_SW_MAXIMIZE = 3
_SW_MINIMIZE = 6
_SW_RESTORE = 9

_WM_CLOSE = 0x0010

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002

_VK_CONTROL = 0x11
_VK_LEFT = 0x25
_VK_RIGHT = 0x27
_VK_D = 0x44
_VK_LWIN = 0x5B

# The shell owns these. PostMessage(WM_CLOSE) to Progman takes the desktop
# down with it, and minimizing the taskbar is never what anyone meant.
_SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT(ctypes.Structure):
    # SendInput rejects the batch unless cbSize equals the real sizeof(INPUT),
    # so the union must carry the (larger) mouse arm even though we only ever
    # fill the keyboard one.
    class _UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


# Lazy binding — the registry is imported long before any action fires, and
# loading a DLL at import time is work the user pays for on every launch.
_user32: Any = None


def _u32() -> Any:
    global _user32
    if _user32 is not None:
        return _user32

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.IsWindow.argtypes = [wintypes.HWND]
    u.IsWindow.restype = wintypes.BOOL
    u.IsIconic.argtypes = [wintypes.HWND]
    u.IsIconic.restype = wintypes.BOOL
    u.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    u.PostMessageW.restype = wintypes.BOOL
    u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    u.SendInput.restype = wintypes.UINT
    u.LockWorkStation.argtypes = []
    u.LockWorkStation.restype = wintypes.BOOL

    _user32 = u
    return u


# Primitives

def _foreground_window() -> Optional[int]:
    """HWND a window command should act on, or None if it isn't a real window."""
    hwnd = _u32().GetForegroundWindow()
    if not hwnd:
        logger.warning("No foreground window to act on")
        return None

    buf = ctypes.create_unicode_buffer(256)
    _u32().GetClassNameW(hwnd, buf, len(buf))
    if buf.value in _SHELL_CLASSES:
        logger.info("Ignoring window command: foreground window is the shell (%s)", buf.value)
        return None
    return hwnd


@safe_call
def _maximize_foreground() -> None:
    hwnd = _foreground_window()
    if hwnd is None:
        return
    # ShowWindow's BOOL is the window's *previous* visibility, not success.
    _u32().ShowWindow(hwnd, _SW_MAXIMIZE)


# Minimizing hands the foreground to an unrelated window, and Windows has no
# "restore the last minimized window" concept — so without remembering the
# HWND, a spoken "minimize" / "restore" pair would silently restore whatever
# surfaced behind it. Wrong window, no error.
_last_minimized: Optional[int] = None


@safe_call
def _minimize_foreground() -> None:
    global _last_minimized
    hwnd = _foreground_window()
    if hwnd is None:
        return
    _u32().ShowWindow(hwnd, _SW_MINIMIZE)
    _last_minimized = hwnd


@safe_call
def _restore_window() -> None:
    global _last_minimized
    remembered, _last_minimized = _last_minimized, None
    if remembered is not None and _u32().IsWindow(remembered) and _u32().IsIconic(remembered):
        _u32().ShowWindow(remembered, _SW_RESTORE)
        _u32().SetForegroundWindow(remembered)
        return

    # Nothing outstanding — "restore" means un-maximize what's in front.
    hwnd = _foreground_window()
    if hwnd is None:
        return
    _u32().ShowWindow(hwnd, _SW_RESTORE)


@safe_call
def _close_foreground_window() -> None:
    """Post WM_CLOSE — a request the app can answer, decline, or prompt on."""
    hwnd = _foreground_window()
    if hwnd is None:
        return
    if not _u32().PostMessageW(hwnd, _WM_CLOSE, 0, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    logger.info("Posted WM_CLOSE to foreground window")


@safe_call
def _send_chord(*vks: int) -> None:
    """Press ``vks`` in order and release in reverse, as one SendInput batch.

    One batch rather than one call per key: the shell's Win-key hotkeys read
    modifier state at the moment the final key arrives, and a batch can't be
    interleaved with the user's own typing.
    """
    events = [(vk, 0) for vk in vks] + [(vk, _KEYEVENTF_KEYUP) for vk in reversed(vks)]
    batch = (_INPUT * len(events))()
    for slot, (vk, flags) in zip(batch, events):
        slot.type = _INPUT_KEYBOARD
        slot.ki.wVk = vk
        slot.ki.dwFlags = flags

    sent = _u32().SendInput(len(events), batch, ctypes.sizeof(_INPUT))
    if sent != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


@safe_call
def _lock_workstation() -> None:
    if not _u32().LockWorkStation():
        raise ctypes.WinError(ctypes.get_last_error())


# Phrase fragments

_END = r"[.!?\s]*$"
_W = r"\s*"

_BACKEND = "system_window"
_TRANSPORT = "os_native"


# Foreground-window state

@action(
    name="system_window_minimize",
    capability="minimize_window",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Minimize the foreground window.",
    patterns=[rf"^{_W}minimi[sz]e(?:\s+(?:the\s+)?window)?{_END}"],
    side_effects=("window_state",),
    examples=("minimize", "minimise", "minimize the window"),
    # "minimize" is a prefix of "minimize everything", a different action.
    no_early_match=True,
)
def minimize_window() -> None:
    _minimize_foreground()


@action(
    name="system_window_maximize",
    capability="maximize_window",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Maximize the foreground window.",
    patterns=[rf"^{_W}maximi[sz]e(?:\s+(?:the\s+)?window)?{_END}"],
    side_effects=("window_state",),
    examples=("maximize", "maximise", "maximize the window"),
)
def maximize_window() -> None:
    _maximize_foreground()


@action(
    name="system_window_restore",
    capability="restore_window",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Restore the last window REX minimized, else un-maximize the foreground one.",
    patterns=[rf"^{_W}(?:restore|unmaximi[sz]e)(?:\s+(?:the\s+)?window)?{_END}"],
    side_effects=("window_state",),
    examples=("restore", "restore window", "unmaximize"),
)
def restore_window() -> None:
    _restore_window()


@action(
    name="system_window_close",
    capability="close_window",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Ask the foreground window to close (the app may prompt to save).",
    patterns=[rf"^{_W}close\s+(?:this\s+|the\s+)?window{_END}"],
    side_effects=("window_state",),
    examples=("close window", "close this window", "close the window"),
    # Destructive-adjacent: "close window" is a prefix of "close windows
    # explorer", so FastVAD must never act on a partial.
    no_early_match=True,
)
def close_window() -> None:
    _close_foreground_window()


# Layout

@action(
    name="system_window_snap_left",
    capability="snap_window_left",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Snap the foreground window to the left half of the screen.",
    patterns=[rf"^{_W}snap\s+(?:the\s+)?(?:window\s+)?(?:to\s+the\s+)?left{_END}"],
    side_effects=("window_position",),
    examples=("snap left", "snap window left", "snap to the left"),
)
def snap_left() -> None:
    _send_chord(_VK_LWIN, _VK_LEFT)


@action(
    name="system_window_snap_right",
    capability="snap_window_right",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Snap the foreground window to the right half of the screen.",
    patterns=[rf"^{_W}snap\s+(?:the\s+)?(?:window\s+)?(?:to\s+the\s+)?right{_END}"],
    side_effects=("window_position",),
    examples=("snap right", "snap window right", "snap to the right"),
)
def snap_right() -> None:
    _send_chord(_VK_LWIN, _VK_RIGHT)


@action(
    name="system_window_show_desktop",
    capability="show_desktop",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Minimize everything and show the desktop (toggles).",
    patterns=[rf"^{_W}(?:show\s+(?:the\s+)?desktop|minimi[sz]e\s+(?:everything|all)){_END}"],
    side_effects=("window_state",),
    examples=("show desktop", "minimize everything", "minimize all"),
)
def show_desktop() -> None:
    _send_chord(_VK_LWIN, _VK_D)


# Virtual desktops

@action(
    name="system_window_next_desktop",
    capability="next_desktop",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Switch to the next virtual desktop.",
    # Not "next desktop": bare "next" is a complete phrase on the media
    # backend, so FastVAD executes a track skip on the partial before
    # "desktop" arrives. Distinct phrasing is the documented fix — see
    # DECISIONS.md, "Phrase-based disambiguation, not prefix-based".
    patterns=[rf"^{_W}(?:switch\s+)?desktop\s+right{_END}"],
    side_effects=("virtual_desktop",),
    examples=("desktop right", "switch desktop right"),
)
def next_desktop() -> None:
    _send_chord(_VK_LWIN, _VK_CONTROL, _VK_RIGHT)


@action(
    name="system_window_last_desktop",
    capability="last_desktop",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Switch to the previous virtual desktop.",
    # Not "last desktop" / "previous desktop", for the same prefix reason.
    patterns=[rf"^{_W}(?:switch\s+)?desktop\s+left{_END}"],
    side_effects=("virtual_desktop",),
    examples=("desktop left", "switch desktop left"),
)
def last_desktop() -> None:
    _send_chord(_VK_LWIN, _VK_CONTROL, _VK_LEFT)


# Session

@action(
    name="system_window_lock_screen",
    capability="lock_screen",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Lock the workstation.",
    patterns=[rf"^{_W}lock(?:\s+(?:the\s+)?(?:screen|workstation|pc))?{_END}"],
    side_effects=("session_state",),
    examples=("lock", "lock screen", "lock the screen", "lock the pc"),
    # Recoverable but disruptive, and bare "lock" is a short word Whisper
    # produces mid-sentence. Wait for the full utterance.
    no_early_match=True,
)
def lock_screen() -> None:
    _lock_workstation()
