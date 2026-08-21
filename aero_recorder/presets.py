from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordingPreset:
    name: str
    quality: str
    fps: int
    include_cursor: bool
    mouse_effects: bool


PRESETS = {
    preset.name: preset
    for preset in (
        RecordingPreset("Small file", "Compact", 30, True, False),
        RecordingPreset("Balanced", "Balanced", 30, True, False),
        RecordingPreset("High quality", "High", 60, True, False),
        RecordingPreset("Presentation", "High", 30, True, True),
        RecordingPreset("Gaming", "High", 60, False, False),
    )
}


def get_preset(name: str) -> RecordingPreset | None:
    return PRESETS.get(name)
