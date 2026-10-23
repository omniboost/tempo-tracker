"""Globale sneltoets (bijv. ⌃⌥T) via Carbon's RegisterEventHotKey; werkt zonder toegankelijkheidstoestemming."""

from __future__ import annotations

import ctypes
import logging
import struct
from typing import Callable

log = logging.getLogger(__name__)

MODIFIERS = {"cmd": (0x0100, "⌘"), "shift": (0x0200, "⇧"), "alt": (0x0800, "⌥"), "option": (0x0800, "⌥"), "ctrl": (0x1000, "⌃")}
KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
    "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46, "space": 49,
}
KEYBOARD_EVENTS = struct.unpack(">I", b"keyb")[0]
HOT_KEY_PRESSED = 5


def parse(shortcut: str) -> tuple[int, int, str]:
    """'ctrl+alt+t' -> (toetscode, modifiers, '⌃⌥T'). ValueError als het geen geldige combinatie is."""
    parts = [part.strip().lower() for part in shortcut.split("+") if part.strip()]
    if len(parts) < 2 or parts[-1] not in KEY_CODES or any(part not in MODIFIERS for part in parts[:-1]):
        raise ValueError(f"Invalid shortcut {shortcut!r} (use e.g. ctrl+alt+t)")
    modifiers, symbols = 0, ""
    for name in ("ctrl", "alt", "option", "shift", "cmd"):
        if name in parts[:-1] and not modifiers & MODIFIERS[name][0]:
            modifiers |= MODIFIERS[name][0]
            symbols += MODIFIERS[name][1]
    return KEY_CODES[parts[-1]], modifiers, symbols + parts[-1].upper()


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32)]


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class HotKey:
    """Eén globale sneltoets; `register` vervangt een eerdere."""

    def __init__(self) -> None:
        self._carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
        self._carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        self._carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, _HANDLER, ctypes.c_uint32, ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        self._carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)
        ]
        self._carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        self._callback: Callable[[], None] | None = None
        self._ref = ctypes.c_void_p()
        self._handler = _HANDLER(self._on_event)  # referentie bewaren, anders ruimt Python hem op
        spec = _EventTypeSpec(KEYBOARD_EVENTS, HOT_KEY_PRESSED)
        status = self._carbon.InstallEventHandler(
            self._carbon.GetApplicationEventTarget(), self._handler, 1, ctypes.byref(spec), None, None
        )
        if status != 0:
            raise OSError(f"InstallEventHandler failed ({status})")

    def _on_event(self, _call_ref, _event, _user_data) -> int:
        if self._callback:
            try:
                self._callback()
            except Exception:
                log.exception("Shortcut handler failed")
        return 0

    def register(self, shortcut: str, callback: Callable[[], None]) -> bool:
        """Registreert de sneltoets; False als hij al door een andere app wordt gebruikt."""
        self.unregister()
        key_code, modifiers, _ = parse(shortcut)
        hot_key_id = _HotKeyID(struct.unpack(">I", b"TmpT")[0], 1)
        ref = ctypes.c_void_p()
        status = self._carbon.RegisterEventHotKey(
            key_code, modifiers, hot_key_id, self._carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref)
        )
        if status != 0:
            log.warning("Shortcut %s could not be registered (%s)", shortcut, status)
            return False
        self._ref, self._callback = ref, callback
        return True

    def unregister(self) -> None:
        if self._ref:
            self._carbon.UnregisterEventHotKey(self._ref)
            self._ref = ctypes.c_void_p()
        self._callback = None
