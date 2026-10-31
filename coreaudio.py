"""Which microphone is the Mac's default right now? Asked straight from CoreAudio through ctypes.

PortAudio (under sounddevice) reads the device list once when it starts, so it cannot tell that a
headset was plugged in later. CoreAudio can, and asking it is a few microseconds, cheap enough to
poll every couple of seconds. Read-only: nothing here opens a device or records anything.
No extra package (PyObjC is not in the venv); CoreAudio and CoreFoundation ship with macOS.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional

_SYSTEM = 1                                   # kAudioObjectSystemObject


def _code(s: str) -> int:                     # 'dIn ' -> the four-character code CoreAudio uses
    return int.from_bytes(s.encode("ascii"), "big")


_DEFAULT_INPUT = _code("dIn ")                # kAudioHardwarePropertyDefaultInputDevice
_DEVICES = _code("dev#")                      # kAudioHardwarePropertyDevices
_RUN_LOOP = _code("rnlp")                     # kAudioHardwarePropertyRunLoop
_NAME = _code("lnam")                         # kAudioObjectPropertyName
_TRANSPORT = _code("tran")                    # kAudioDevicePropertyTransportType
_STREAMS = _code("stm#")                      # kAudioDevicePropertyStreams
_GLOBAL = _code("glob")                       # kAudioObjectPropertyScopeGlobal
_INPUT = _code("inpt")                        # kAudioObjectPropertyScopeInput
_BUILT_IN = _code("bltn")                     # kAudioDeviceTransportTypeBuiltIn
_UTF8 = 0x08000100                            # kCFStringEncodingUTF8


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


_ca = _cf = None


def _libs():
    """Load the two frameworks once; None on a machine without them (then nothing follows)."""
    global _ca, _cf
    if _ca is None:
        try:
            _ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
            _cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        except (OSError, TypeError):
            _ca = _cf = False
            return None
        _ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        _ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        _ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        _ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        _ca.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                                   ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        _ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
        _cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        _cf.CFStringGetCString.restype = ctypes.c_bool
        _cf.CFRelease.argtypes = [ctypes.c_void_p]
        # A background program has no main run loop for CoreAudio to post device changes on; NULL
        # tells it to keep its own thread, so the values read below stay current (Apple's TN2091).
        none = ctypes.c_void_p(None)
        _ca.AudioObjectSetPropertyData(_SYSTEM, ctypes.byref(_Address(_RUN_LOOP, _GLOBAL, 0)), 0, None,
                                       ctypes.sizeof(none), ctypes.byref(none))
    return (_ca, _cf) if _ca else None


def _uint(obj: int, selector: int, scope: int = _GLOBAL) -> Optional[int]:
    ca, _ = _libs() or (None, None)
    if not ca:
        return None
    out, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
    if ca.AudioObjectGetPropertyData(obj, ctypes.byref(_Address(selector, scope, 0)), 0, None,
                                     ctypes.byref(size), ctypes.byref(out)) != 0:
        return None
    return out.value


def _name(device: int) -> Optional[str]:
    ca, cf = _libs() or (None, None)
    if not ca:
        return None
    ref, size = ctypes.c_void_p(), ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    if ca.AudioObjectGetPropertyData(device, ctypes.byref(_Address(_NAME, _GLOBAL, 0)), 0, None,
                                     ctypes.byref(size), ctypes.byref(ref)) != 0 or not ref.value:
        return None
    try:
        buf = ctypes.create_string_buffer(512)
        return buf.value.decode("utf-8") if cf.CFStringGetCString(ref, buf, len(buf), _UTF8) else None
    finally:
        cf.CFRelease(ref)


def default_input_name() -> Optional[str]:
    """The name of the Mac's current default microphone, e.g. 'MacBook Pro Microphone'; None if
    CoreAudio could not say (then the caller keeps what it has)."""
    dev = _uint(_SYSTEM, _DEFAULT_INPUT)
    return _name(dev) if dev else None


def _input_devices() -> list[int]:
    ca, _ = _libs() or (None, None)
    if not ca:
        return []
    addr, size = _Address(_DEVICES, _GLOBAL, 0), ctypes.c_uint32(0)
    if ca.AudioObjectGetPropertyDataSize(_SYSTEM, ctypes.byref(addr), 0, None, ctypes.byref(size)) != 0:
        return []
    ids = (ctypes.c_uint32 * (size.value // 4))()
    if ca.AudioObjectGetPropertyData(_SYSTEM, ctypes.byref(addr), 0, None, ctypes.byref(size), ids) != 0:
        return []
    found = []
    for dev in ids:
        streams = ctypes.c_uint32(0)
        if ca.AudioObjectGetPropertyDataSize(dev, ctypes.byref(_Address(_STREAMS, _INPUT, 0)), 0, None,
                                             ctypes.byref(streams)) == 0 and streams.value:
            found.append(dev)
    return found


def built_in_input_names() -> list[str]:
    """The Mac's own microphones (transport type built-in), for falling back when the new default
    will not open. Empty on a Mac without one (a Mac mini)."""
    return [n for d in _input_devices() if _uint(d, _TRANSPORT) == _BUILT_IN and (n := _name(d))]


def input_names() -> list[str]:
    return [n for d in _input_devices() if (n := _name(d))]
