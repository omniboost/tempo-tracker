"""Zit je in een gesprek? Gemeten aan of een microfoon in gebruik is (Teams, Zoom, Meet, …).

Leest alleen de status van audioapparaten via CoreAudio; er wordt niets opgenomen en er is geen toestemming nodig.
"""

from __future__ import annotations

import ctypes
import logging
import struct

log = logging.getLogger(__name__)


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode())[0]


SYSTEM_OBJECT = 1
DEVICES = _fourcc("dev#")
STREAMS = _fourcc("stm#")
RUNNING_SOMEWHERE = _fourcc("gone")
SCOPE_GLOBAL = _fourcc("glob")
SCOPE_INPUT = _fourcc("inpt")
ELEMENT_MAIN = 0


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


try:
    _core_audio = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    _core_audio.AudioObjectGetPropertyDataSize.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    _core_audio.AudioObjectGetPropertyData.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]
except OSError:  # pragma: no cover - alleen op macOS beschikbaar
    _core_audio = None


def _size(object_id: int, selector: int, scope: int) -> int:
    size = ctypes.c_uint32(0)
    address = _Address(selector, scope, ELEMENT_MAIN)
    if _core_audio.AudioObjectGetPropertyDataSize(object_id, ctypes.byref(address), 0, None, ctypes.byref(size)) != 0:
        return 0
    return size.value


def _input_devices() -> list[int]:
    size = _size(SYSTEM_OBJECT, DEVICES, SCOPE_GLOBAL)
    count = size // ctypes.sizeof(ctypes.c_uint32)
    devices = (ctypes.c_uint32 * count)()
    data_size = ctypes.c_uint32(size)
    address = _Address(DEVICES, SCOPE_GLOBAL, ELEMENT_MAIN)
    if _core_audio.AudioObjectGetPropertyData(SYSTEM_OBJECT, ctypes.byref(address), 0, None, ctypes.byref(data_size), devices) != 0:
        return []
    return [device for device in devices if _size(device, STREAMS, SCOPE_INPUT) > 0]


def _running(device: int) -> bool:
    running = ctypes.c_uint32(0)
    data_size = ctypes.c_uint32(ctypes.sizeof(running))
    address = _Address(RUNNING_SOMEWHERE, SCOPE_GLOBAL, ELEMENT_MAIN)
    status = _core_audio.AudioObjectGetPropertyData(device, ctypes.byref(address), 0, None, ctypes.byref(data_size), ctypes.byref(running))
    return status == 0 and running.value != 0


def in_call() -> bool:
    """True als een app een microfoon gebruikt."""
    if _core_audio is None:
        return False
    try:
        return any(_running(device) for device in _input_devices())
    except Exception:  # nooit de app laten vallen op een audiofout
        log.exception("Checking the microphone failed")
        return False
