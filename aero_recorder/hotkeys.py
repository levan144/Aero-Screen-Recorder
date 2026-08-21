from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Callable


MODIFIER_CODES = {
    "Ctrl": 0x11,
    "Alt": 0x12,
    "Shift": 0x10,
    "Win": 0x5B,
}
MODIFIER_ALIASES = {
    "CONTROL": "Ctrl",
    "CTRL": "Ctrl",
    "ALT": "Alt",
    "SHIFT": "Shift",
    "WIN": "Win",
    "WINDOWS": "Win",
}
NAMED_KEYS = {
    "SPACE": ("Space", 0x20),
    "ENTER": ("Enter", 0x0D),
    "ESC": ("Esc", 0x1B),
    "ESCAPE": ("Esc", 0x1B),
    "TAB": ("Tab", 0x09),
}


@dataclass(frozen=True, slots=True)
class Hotkey:
    modifiers: tuple[str, ...]
    key_name: str
    key_code: int

    @property
    def label(self) -> str:
        return "+".join((*self.modifiers, self.key_name))

    @classmethod
    def parse(cls, value: str) -> "Hotkey":
        parts = [part.strip() for part in value.split("+") if part.strip()]
        if len(parts) < 2:
            raise ValueError("Use at least one modifier, for example Ctrl+Shift+R.")
        modifiers: list[str] = []
        key_name = ""
        key_code = 0
        for part in parts:
            upper = part.upper()
            modifier = MODIFIER_ALIASES.get(upper)
            if modifier:
                if modifier not in modifiers:
                    modifiers.append(modifier)
                continue
            if key_code:
                raise ValueError("A shortcut can contain only one regular key.")
            if len(upper) == 1 and ("A" <= upper <= "Z" or "0" <= upper <= "9"):
                key_name, key_code = upper, ord(upper)
            elif upper.startswith("F") and upper[1:].isdigit() and 1 <= int(upper[1:]) <= 12:
                number = int(upper[1:])
                key_name, key_code = f"F{number}", 0x6F + number
            elif upper in NAMED_KEYS:
                key_name, key_code = NAMED_KEYS[upper]
            else:
                raise ValueError(f"Unsupported shortcut key: {part}")
        if not modifiers or not key_code:
            raise ValueError("Use at least one modifier and one regular key.")
        ordered = tuple(name for name in ("Ctrl", "Alt", "Shift", "Win") if name in modifiers)
        return cls(ordered, key_name, key_code)


def key_is_down(key_code: int) -> bool:
    if os.name != "nt":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(key_code) & 0x8000)


class HotkeyPoller:
    def __init__(self) -> None:
        self.bindings: dict[str, tuple[Hotkey, Callable[[], None]]] = {}
        self._was_down: dict[str, bool] = {}

    def set_binding(self, name: str, hotkey: Hotkey, callback: Callable[[], None]) -> None:
        self.bindings[name] = (hotkey, callback)
        self._was_down[name] = False

    def poll(self) -> None:
        for name, (hotkey, callback) in self.bindings.items():
            down = all(key_is_down(MODIFIER_CODES[item]) for item in hotkey.modifiers)
            down = down and key_is_down(hotkey.key_code)
            if down and not self._was_down.get(name, False):
                callback()
            self._was_down[name] = down
