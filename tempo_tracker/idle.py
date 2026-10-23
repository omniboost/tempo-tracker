"""Inactiviteit uitlezen: alleen de systeemteller 'tijd sinds laatste toets/muis' (HIDIdleTime).

Geen keylogging en geen Accessibility- of Input Monitoring-toestemming nodig.
"""

from __future__ import annotations

import re
import subprocess

_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def idle_seconds() -> float:
    try:
        output = subprocess.run(
            ["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    match = _IDLE_RE.search(output)
    return int(match.group(1)) / 1_000_000_000 if match else 0.0


try:
    import ctypes

    import objc

    _cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    _cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    _cg.CGMainDisplayID.restype = ctypes.c_uint32
    _cg.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]
    _cg.CGDisplayIsAsleep.restype = ctypes.c_uint32
    _cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
except (OSError, ImportError):  # pragma: no cover - alleen op macOS beschikbaar
    _cg = None


def screen_off() -> bool:
    """True als het scherm uit staat of vergrendeld is: dan ben je er niet.

    Bijvoorbeeld als macOS de Mac met de klep dicht even half wakker maakt voor onderhoud.
    """
    if _cg is None:
        return False
    try:
        if _cg.CGDisplayIsAsleep(_cg.CGMainDisplayID()):
            return True
        pointer = _cg.CGSessionCopyCurrentDictionary()
        if not pointer:
            return True  # geen sessie op het scherm (bijv. het inlogvenster)
        session = objc.objc_object(c_void_p=pointer)
        _cf.CFRelease(pointer)
        return bool(session.get("CGSSessionScreenIsLocked", False))
    except Exception:  # nooit de app laten vallen hierop
        return False
