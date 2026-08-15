"""Phase 1 / task 0b — spike: absolute system volume via ctypes + Core Audio.

Proves we can do "set volume to 40" with no new dependency, the way
rex_main/ui/hud.py already binds user32 for click-through.

Chain: CoCreateInstance(MMDeviceEnumerator)
       -> GetDefaultAudioEndpoint(eRender, eConsole)
       -> Activate(IID_IAudioEndpointVolume)
       -> Get/SetMasterVolumeLevelScalar, Get/SetMute, VolumeStepUp/Down

Reads current state, exercises every call, then restores what it found.
Net effect on the machine: nothing.
"""

import ctypes
from ctypes import POINTER, byref, c_float, c_void_p, c_int, c_uint
from ctypes.wintypes import BOOL, DWORD

ole32 = ctypes.windll.ole32

CLSCTX_ALL = 23
eRender = 0
eConsole = 0
S_OK = 0


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
        if hr != S_OK:
            raise OSError(f"CLSIDFromString failed for {guid_str}: 0x{hr & 0xFFFFFFFF:08x}")


CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

# vtable slots (0-2 are IUnknown: QueryInterface / AddRef / Release)
IMMDeviceEnumerator_GetDefaultAudioEndpoint = 4
IMMDevice_Activate = 3
IAudioEndpointVolume_GetChannelCount = 5
IAudioEndpointVolume_SetMasterVolumeLevelScalar = 7
IAudioEndpointVolume_GetMasterVolumeLevelScalar = 9
IAudioEndpointVolume_SetMute = 14
IAudioEndpointVolume_GetMute = 15
IAudioEndpointVolume_VolumeStepUp = 17
IAudioEndpointVolume_VolumeStepDown = 18
IUnknown_Release = 2


def _call(ptr, slot, *args, argtypes=()):
    """Invoke vtable[slot] on a COM interface pointer. Raises on failed HRESULT."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    fn = proto(vtbl[slot])
    hr = fn(ptr, *args)
    # SUCCEEDED(hr) is hr >= 0, NOT hr == 0. SetMute returns S_FALSE (1) when
    # the endpoint is already in the requested state — a success, not an error.
    if hr < 0:
        raise OSError(f"COM call slot {slot} failed: 0x{hr & 0xFFFFFFFF:08x}")


def _release(ptr):
    """IUnknown::Release returns the new refcount (ULONG), not an HRESULT."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
    return proto(vtbl[IUnknown_Release])(ptr)


def get_endpoint_volume():
    """Return an IAudioEndpointVolume* for the default render device."""
    enumerator = c_void_p()
    hr = ole32.CoCreateInstance(
        byref(GUID(CLSID_MMDeviceEnumerator)),
        None,
        CLSCTX_ALL,
        byref(GUID(IID_IMMDeviceEnumerator)),
        byref(enumerator),
    )
    if hr != S_OK:
        raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08x}")

    device = c_void_p()
    _call(
        enumerator, IMMDeviceEnumerator_GetDefaultAudioEndpoint,
        c_int(eRender), c_int(eConsole), byref(device),
        argtypes=(c_int, c_int, POINTER(c_void_p)),
    )

    volume = c_void_p()
    _call(
        device, IMMDevice_Activate,
        byref(GUID(IID_IAudioEndpointVolume)), DWORD(CLSCTX_ALL), None, byref(volume),
        argtypes=(POINTER(GUID), DWORD, c_void_p, POINTER(c_void_p)),
    )

    _release(device)
    _release(enumerator)
    return volume


def get_volume(vol) -> float:
    level = c_float()
    _call(vol, IAudioEndpointVolume_GetMasterVolumeLevelScalar,
          byref(level), argtypes=(POINTER(c_float),))
    return level.value


def set_volume(vol, scalar: float) -> None:
    scalar = max(0.0, min(1.0, scalar))
    _call(vol, IAudioEndpointVolume_SetMasterVolumeLevelScalar,
          c_float(scalar), None, argtypes=(c_float, POINTER(GUID)))


def get_mute(vol) -> bool:
    muted = BOOL()
    _call(vol, IAudioEndpointVolume_GetMute, byref(muted), argtypes=(POINTER(BOOL),))
    return bool(muted.value)


def set_mute(vol, muted: bool) -> None:
    _call(vol, IAudioEndpointVolume_SetMute,
          BOOL(bool(muted)), None, argtypes=(BOOL, POINTER(GUID)))


def step_up(vol) -> None:
    _call(vol, IAudioEndpointVolume_VolumeStepUp, None, argtypes=(POINTER(GUID),))


def step_down(vol) -> None:
    _call(vol, IAudioEndpointVolume_VolumeStepDown, None, argtypes=(POINTER(GUID),))


def channel_count(vol) -> int:
    n = c_uint()
    _call(vol, IAudioEndpointVolume_GetChannelCount, byref(n), argtypes=(POINTER(c_uint),))
    return n.value


def main():
    ole32.CoInitialize(None)
    vol = get_endpoint_volume()

    original = get_volume(vol)
    original_mute = get_mute(vol)
    print(f"  device channels     : {channel_count(vol)}")
    print(f"  current volume      : {original * 100:.0f}%")
    print(f"  currently muted     : {original_mute}")

    try:
        print("\n  --- absolute set (the thing pycaw was wanted for) ---")
        for target in (0.40, 0.75, 0.10):
            set_volume(vol, target)
            got = get_volume(vol)
            ok = abs(got - target) < 0.01
            print(f"  set {target*100:3.0f}%  ->  read back {got*100:5.1f}%   {'OK' if ok else 'MISMATCH'}")

        print("\n  --- relative steps (Windows' own increment) ---")
        before = get_volume(vol)
        step_up(vol)
        after_up = get_volume(vol)
        step_down(vol)
        after_down = get_volume(vol)
        print(f"  step up   : {before*100:.1f}% -> {after_up*100:.1f}%")
        print(f"  step down : {after_up*100:.1f}% -> {after_down*100:.1f}%")

        print("\n  --- mute ---")
        set_mute(vol, True)
        print(f"  after set_mute(True)  : muted={get_mute(vol)}")
        set_mute(vol, False)
        print(f"  after set_mute(False) : muted={get_mute(vol)}")
    finally:
        set_volume(vol, original)
        set_mute(vol, original_mute)
        print(f"\n  restored to {get_volume(vol)*100:.0f}%, muted={get_mute(vol)}")
        _release(vol)
        ole32.CoUninitialize()


if __name__ == "__main__":
    main()
