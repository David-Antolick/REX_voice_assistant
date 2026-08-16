"""System audio actions — Windows Core Audio volume + global media keys.

This backend owns the *generic* audio phrases ("volume up", "mute",
"play", "pause", "next"). Music backends require naming the app
("music volume up", "pause music") — see the phrase-ownership entry in
docs/DECISIONS.md.

Volume and mute go through ``IAudioEndpointVolume`` on the default render
endpoint, bound with raw ``ctypes`` COM rather than ``pycaw``: the whole
surface is six vtable slots, and ``rex_main/ui/hud.py`` already sets the
precedent for hand-binding a Windows API instead of taking a dependency.

Transport control uses the ``VK_MEDIA_*`` keys instead of the endpoint,
because that is the point of this backend — media keys reach browsers,
VLC, Netflix and calls, where the music-backend HTTP/OAuth APIs can't.

Chain: CoCreateInstance(MMDeviceEnumerator)
       -> GetDefaultAudioEndpoint(eRender, eConsole)
       -> Activate(IID_IAudioEndpointVolume)
"""

from __future__ import annotations

import ctypes
import functools
import logging
import threading
from ctypes import POINTER, byref, c_float, c_int, c_void_p
from ctypes.wintypes import BOOL, DWORD
from typing import Any, Callable, TypeVar

from rex_main.actions.registry import ArgSpec, action

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

ole32 = ctypes.windll.ole32
user32 = ctypes.windll.user32

CLSCTX_ALL = 23
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850
eRender = 0
eConsole = 0

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

# vtable slots (0-2 are IUnknown: QueryInterface / AddRef / Release)
_IUnknown_Release = 2
_IMMDevice_Activate = 3
_IMMDeviceEnumerator_GetDefaultAudioEndpoint = 4
_IAudioEndpointVolume_SetMasterVolumeLevelScalar = 7
_IAudioEndpointVolume_SetMute = 14
_IAudioEndpointVolume_VolumeStepUp = 17
_IAudioEndpointVolume_VolumeStepDown = 18

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002


def safe_call(func: F) -> F:
    """Swallow COM / OS errors so one bad call can't kill the assistant."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except OSError as e:
            logger.error("System audio error in %s: %s", func.__name__, e)
        except Exception as e:
            logger.exception("Unexpected error in %s: %s", func.__name__, e)
        return None
    return wrapper  # type: ignore[return-value]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, guid_str: str):
        super().__init__()
        hr = ole32.CLSIDFromString(ctypes.c_wchar_p(guid_str), byref(self))
        if hr < 0:
            raise OSError(f"CLSIDFromString failed for {guid_str}: 0x{hr & 0xFFFFFFFF:08x}")


def _call(ptr: c_void_p, slot: int, *args: Any, argtypes: tuple = ()) -> None:
    """Invoke vtable[slot] on a COM interface pointer. Raises on a failed HRESULT."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtbl[slot])
    hr = fn(ptr, *args)
    # SUCCEEDED(hr) is hr >= 0, NOT hr == 0. SetMute returns S_FALSE (1) when
    # the endpoint is already in the requested state — a success, not an error.
    if hr < 0:
        raise OSError(f"COM call slot {slot} failed: 0x{hr & 0xFFFFFFFF:08x}")


def _release(ptr: c_void_p) -> None:
    """IUnknown::Release returns the new refcount (ULONG), not an HRESULT, so
    it must not go through the HRESULT-checking helper above."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtbl[_IUnknown_Release])(ptr)


_com = threading.local()


def _ensure_com() -> None:
    """CoInitialize the calling thread. REX dispatches from the audio thread,
    not the Qt main thread, so this can't be done once at startup."""
    if getattr(_com, "ready", False):
        return
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    # S_FALSE = already initialized on this thread; RPC_E_CHANGED_MODE = already
    # initialized with the other apartment model (Qt does this). Both are usable.
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        raise OSError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08x}")
    _com.ready = True


class SystemAudio:
    """Default-render-endpoint volume plus global media keys."""

    def __init__(self) -> None:
        self._clsid_enumerator = GUID(CLSID_MMDeviceEnumerator)
        self._iid_enumerator = GUID(IID_IMMDeviceEnumerator)
        self._iid_endpoint_volume = GUID(IID_IAudioEndpointVolume)

    def _endpoint(self) -> c_void_p:
        """Acquire an IAudioEndpointVolume* for the *current* default device.

        Re-acquired per command (~2 ms) rather than cached: the pointer is
        bound to one device, so a cached one silently controls the old
        endpoint after the user plugs in headphones.
        """
        _ensure_com()

        enumerator = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(self._clsid_enumerator),
            None,
            CLSCTX_ALL,
            byref(self._iid_enumerator),
            byref(enumerator),
        )
        if hr < 0:
            raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08x}")

        device = c_void_p()
        try:
            _call(
                enumerator, _IMMDeviceEnumerator_GetDefaultAudioEndpoint,
                c_int(eRender), c_int(eConsole), byref(device),
                argtypes=(c_int, c_int, POINTER(c_void_p)),
            )
            volume = c_void_p()
            _call(
                device, _IMMDevice_Activate,
                byref(self._iid_endpoint_volume), DWORD(CLSCTX_ALL), None, byref(volume),
                argtypes=(POINTER(GUID), DWORD, c_void_p, POINTER(c_void_p)),
            )
        finally:
            if device:
                _release(device)
            _release(enumerator)
        return volume

    def _endpoint_call(self, slot: int, *args: Any, argtypes: tuple = ()) -> None:
        volume = self._endpoint()
        try:
            _call(volume, slot, *args, argtypes=argtypes)
        finally:
            _release(volume)

    @staticmethod
    def _tap(vk: int) -> None:
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    # Volume / mute — Core Audio

    def volume_up(self) -> None:
        # VolumeStepUp rather than a hand-picked delta, so REX's step matches
        # what the keyboard's volume key does.
        self._endpoint_call(
            _IAudioEndpointVolume_VolumeStepUp, None, argtypes=(POINTER(GUID),)
        )

    def volume_down(self) -> None:
        self._endpoint_call(
            _IAudioEndpointVolume_VolumeStepDown, None, argtypes=(POINTER(GUID),)
        )

    def set_volume(self, level: int | str) -> None:
        try:
            pct = max(0, min(100, int(level)))
        except (ValueError, TypeError):
            logger.error("Bad volume value: %r", level)
            return
        self._endpoint_call(
            _IAudioEndpointVolume_SetMasterVolumeLevelScalar,
            c_float(pct / 100.0), None,
            argtypes=(c_float, POINTER(GUID)),
        )
        logger.info("System volume set to %d%%", pct)

    def set_mute(self, muted: bool) -> None:
        self._endpoint_call(
            _IAudioEndpointVolume_SetMute,
            BOOL(muted), None,
            argtypes=(BOOL, POINTER(GUID)),
        )
        logger.info("System audio %s", "muted" if muted else "unmuted")

    # Transport — global media keys

    def play_pause(self) -> None:
        self._tap(VK_MEDIA_PLAY_PAUSE)

    def next_track(self) -> None:
        self._tap(VK_MEDIA_NEXT_TRACK)

    def previous_track(self) -> None:
        self._tap(VK_MEDIA_PREV_TRACK)

    def stop(self) -> None:
        self._tap(VK_MEDIA_STOP)


# Lazy singleton — the registry is imported long before any command runs, and
# building the client touches ole32.
_client: SystemAudio | None = None


def _get() -> SystemAudio:
    global _client
    if _client is None:
        _client = SystemAudio()
    return _client


def reset_client() -> None:
    """Drop the cached client (used by service-switch flows for symmetry)."""
    global _client
    _client = None


# Wrap the OS-touching methods so a COM failure can't reach the audio loop.
for _name in (
    "volume_up",
    "volume_down",
    "set_volume",
    "set_mute",
    "play_pause",
    "next_track",
    "previous_track",
    "stop",
):
    setattr(SystemAudio, _name, safe_call(getattr(SystemAudio, _name)))


# Action registrations

_END = r"[.!?\s]*$"
_W = r"\s*"

_BACKEND = "system_audio"
_TRANSPORT = "os_native"
_PRECONDS = ("A default Windows audio output device is present",)


@action(
    name="system_audio_volume_up",
    capability="volume_up",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Step the system volume up by one Windows volume-key increment.",
    patterns=[rf"^{_W}(?:volume\s+up|turn\s+it\s+up|louder){_END}"],
    preconditions=_PRECONDS,
    side_effects=("system_volume",),
    examples=("volume up", "louder", "turn it up"),
)
def volume_up() -> None:
    _get().volume_up()


@action(
    name="system_audio_volume_down",
    capability="volume_down",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Step the system volume down by one Windows volume-key increment.",
    patterns=[rf"^{_W}(?:volume\s+down|turn\s+it\s+down|quieter){_END}"],
    preconditions=_PRECONDS,
    side_effects=("system_volume",),
    examples=("volume down", "quieter", "turn it down"),
)
def volume_down() -> None:
    _get().volume_down()


@action(
    name="system_audio_set_volume",
    capability="set_volume",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Set the system volume to a specific 0–100 level.",
    patterns=[rf"^{_W}(?:set\s+)?volume\s+(?:to\s+)?(\d{{1,3}})(?:\s*(?:percent|%))?{_END}"],
    args=(ArgSpec("level", "int", "Target volume 0–100 (clamped)."),),
    preconditions=_PRECONDS,
    side_effects=("system_volume",),
    examples=("volume 40", "set volume to 40", "volume 75 percent"),
    no_early_match=True,
)
def set_volume(level: int | str) -> None:
    _get().set_volume(level)


@action(
    name="system_audio_mute",
    capability="mute",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Mute the system audio output.",
    patterns=[rf"^{_W}mute(?:\s+(?:the\s+)?(?:audio|sound|volume))?{_END}"],
    preconditions=_PRECONDS,
    side_effects=("system_mute",),
    examples=("mute", "mute audio", "mute the sound"),
)
def mute() -> None:
    _get().set_mute(True)


@action(
    name="system_audio_unmute",
    capability="unmute",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Unmute the system audio output.",
    patterns=[rf"^{_W}unmute(?:\s+(?:the\s+)?(?:audio|sound|volume))?{_END}"],
    preconditions=_PRECONDS,
    side_effects=("system_mute",),
    examples=("unmute", "unmute audio", "unmute the sound"),
)
def unmute() -> None:
    _get().set_mute(False)


@action(
    name="system_audio_play_pause",
    capability="play_pause",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Toggle play/pause on whatever currently owns the media keys.",
    patterns=[rf"^{_W}(?:play\s*/?\s*pause|play|pause|resume){_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state",),
    examples=("play", "pause", "play pause", "resume"),
    # Bare transport words are prefixes of music-backend phrases ("pause
    # music", "resume music"). FastVAD dispatches partial transcripts, so
    # without this the media key fires mid-utterance and the intended
    # command's audio is dropped.
    no_early_match=True,
)
def play_pause() -> None:
    _get().play_pause()


@action(
    name="system_audio_next_track",
    capability="next_track",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Send the media next-track key to whatever is playing.",
    patterns=[rf"^{_W}(?:next|skip){_END}"],
    preconditions=_PRECONDS,
    side_effects=("current_track",),
    examples=("next", "skip"),
    no_early_match=True,  # prefix of "next song" / "skip song" / "skip ahead N"
)
def next_track() -> None:
    _get().next_track()


@action(
    name="system_audio_previous_track",
    capability="previous_track",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Send the media previous-track key to whatever is playing.",
    patterns=[rf"^{_W}(?:previous|last){_END}"],
    preconditions=_PRECONDS,
    side_effects=("current_track",),
    examples=("previous", "last"),
    no_early_match=True,  # prefix of "previous song" / "last song"
)
def previous_track() -> None:
    _get().previous_track()


@action(
    name="system_audio_stop",
    capability="stop",
    backend=_BACKEND,
    slot=None,
    transport=_TRANSPORT,
    summary="Send the media stop key to whatever is playing.",
    patterns=[rf"^{_W}stop{_END}"],
    preconditions=_PRECONDS,
    side_effects=("playback_state",),
    examples=("stop",),
    no_early_match=True,  # prefix of "stop music"
)
def stop() -> None:
    _get().stop()
