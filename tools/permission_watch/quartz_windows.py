"""The list of on-screen windows, read straight from macOS (Quartz window list) with ctypes.

No pyobjc needed: neither the repo's .venv nor /usr/bin/python3 has it (checked 2026-11-17), so this
calls CoreGraphics and CoreFoundation directly. It only READS the window list; it never clicks, types
or scripts anything.

Each window comes back as a plain dict with the same keys Quartz uses (kCGWindowOwnerName,
kCGWindowName, kCGWindowNumber, kCGWindowLayer, kCGWindowAlpha, kCGWindowBounds, ...), so test
fixtures look exactly like real output. Without a Screen Recording grant macOS leaves out
kCGWindowName for other apps' windows; the owner name is always there.
"""
from __future__ import annotations

import ctypes
from typing import Any, Dict, List, Optional

_CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

_VP = ctypes.c_void_p
_UTF8 = 0x08000100              # kCFStringEncodingUTF8
_SINT64 = 4                     # kCFNumberSInt64Type
_DOUBLE = 13                    # kCFNumberDoubleType
ON_SCREEN_ONLY = 1              # kCGWindowListOptionOnScreenOnly
EXCLUDE_DESKTOP = 16            # kCGWindowListExcludeDesktopElements


def _sig(lib: Any, name: str, restype: Any, *argtypes: Any) -> Any:
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


# macOS 27 no longer exports CGWindowListCopyInfo (dlsym "symbol not found", checked 2026-11-17). The two
# calls it was made of are still there and give the same dictionaries: the on-screen window ids, then
# their descriptions.
_CGWindowListCreate = _sig(_CG, "CGWindowListCreate", _VP, ctypes.c_uint32, ctypes.c_uint32)
_CGWindowListCreateDescriptionFromArray = _sig(_CG, "CGWindowListCreateDescriptionFromArray", _VP, _VP)
_CGPreflight = _sig(_CG, "CGPreflightScreenCaptureAccess", ctypes.c_bool)
_CFRelease = _sig(_CF, "CFRelease", None, _VP)
_CFGetTypeID = _sig(_CF, "CFGetTypeID", ctypes.c_ulong, _VP)
_CFStringGetTypeID = _sig(_CF, "CFStringGetTypeID", ctypes.c_ulong)
_CFNumberGetTypeID = _sig(_CF, "CFNumberGetTypeID", ctypes.c_ulong)
_CFBooleanGetTypeID = _sig(_CF, "CFBooleanGetTypeID", ctypes.c_ulong)
_CFDictionaryGetTypeID = _sig(_CF, "CFDictionaryGetTypeID", ctypes.c_ulong)
_CFArrayGetCount = _sig(_CF, "CFArrayGetCount", ctypes.c_long, _VP)
_CFArrayGetValueAtIndex = _sig(_CF, "CFArrayGetValueAtIndex", _VP, _VP, ctypes.c_long)
_CFDictionaryGetValue = _sig(_CF, "CFDictionaryGetValue", _VP, _VP, _VP)
_CFStringCreateWithCString = _sig(_CF, "CFStringCreateWithCString", _VP, _VP, ctypes.c_char_p, ctypes.c_uint32)
_CFStringGetLength = _sig(_CF, "CFStringGetLength", ctypes.c_long, _VP)
_CFStringGetMaxSize = _sig(_CF, "CFStringGetMaximumSizeForEncoding", ctypes.c_long, ctypes.c_long, ctypes.c_uint32)
_CFStringGetCString = _sig(_CF, "CFStringGetCString", ctypes.c_bool, _VP, ctypes.c_char_p, ctypes.c_long,
                           ctypes.c_uint32)
_CFNumberGetValue = _sig(_CF, "CFNumberGetValue", ctypes.c_bool, _VP, ctypes.c_int, _VP)
_CFNumberIsFloatType = _sig(_CF, "CFNumberIsFloatType", ctypes.c_bool, _VP)
_CFBooleanGetValue = _sig(_CF, "CFBooleanGetValue", ctypes.c_bool, _VP)

_KEYS = ("kCGWindowNumber", "kCGWindowOwnerName", "kCGWindowName", "kCGWindowOwnerPID", "kCGWindowLayer",
         "kCGWindowAlpha", "kCGWindowIsOnscreen", "kCGWindowBounds")
_BOUNDS = ("X", "Y", "Width", "Height")
_cfkeys: Dict[str, int] = {}     # created once, kept for the life of the process


def _cfkey(name: str) -> int:
    if name not in _cfkeys:
        _cfkeys[name] = _CFStringCreateWithCString(None, name.encode(), _UTF8)
    return _cfkeys[name]


def _py(ref: Optional[int]) -> Any:
    """A CoreFoundation string, number, boolean or (bounds) dictionary as a Python value."""
    if not ref:
        return None
    tid = _CFGetTypeID(ref)
    if tid == _CFStringGetTypeID():
        size = _CFStringGetMaxSize(_CFStringGetLength(ref), _UTF8) + 1
        buf = ctypes.create_string_buffer(size)
        return buf.value.decode("utf-8", "replace") if _CFStringGetCString(ref, buf, size, _UTF8) else ""
    if tid == _CFNumberGetTypeID():
        if _CFNumberIsFloatType(ref):
            d = ctypes.c_double()
            _CFNumberGetValue(ref, _DOUBLE, ctypes.byref(d))
            return d.value
        i = ctypes.c_int64()
        _CFNumberGetValue(ref, _SINT64, ctypes.byref(i))
        return i.value
    if tid == _CFBooleanGetTypeID():
        return bool(_CFBooleanGetValue(ref))
    if tid == _CFDictionaryGetTypeID():
        return {k: _py(_CFDictionaryGetValue(ref, _cfkey(k))) for k in _BOUNDS}
    return None


def list_windows() -> List[Dict[str, Any]]:
    """Every on-screen window (desktop picture and icons left out), front to back."""
    ids = _CGWindowListCreate(ON_SCREEN_ONLY | EXCLUDE_DESKTOP, 0)
    if not ids:
        return []
    try:
        arr = _CGWindowListCreateDescriptionFromArray(ids)
    finally:
        _CFRelease(ids)
    if not arr:
        return []
    try:
        out = []
        for i in range(_CFArrayGetCount(arr)):
            d = _CFArrayGetValueAtIndex(arr, i)
            w = {}
            for k in _KEYS:
                v = _py(_CFDictionaryGetValue(d, _cfkey(k)))
                if v is not None:
                    w[k] = v
            out.append(w)
        return out
    finally:
        _CFRelease(arr)


def can_capture_screen() -> bool:
    """True when this process (really: the program launchd started) holds the Screen Recording grant.
    Only asks; never brings up a prompt."""
    return bool(_CGPreflight())
