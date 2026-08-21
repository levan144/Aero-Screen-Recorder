from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .winapi import get_videos_folder
from .runtime import is_portable, portable_data_folder


@dataclass(slots=True)
class AppSettings:
    output_folder: str
    capture_mode: str = "Full screen"
    fps: int = 30
    quality: str = "Balanced"
    video_encoder: str = "Auto"
    recording_preset: str = "Balanced"
    microphone: str = ""
    microphone_enabled: bool = True
    microphone_noise_reduction: bool = False
    system_audio_device: str = ""
    system_audio_enabled: bool = False
    webcam: str = ""
    webcam_enabled: bool = False
    webcam_shape: str = "Circle"
    webcam_position: str = "Bottom right"
    webcam_size: str = "Medium"
    privacy_effect: str = "Blur"
    include_cursor: bool = True
    mouse_effects_enabled: bool = False
    countdown_seconds: int = 3
    shortcut_record: str = "Ctrl+Shift+R"
    shortcut_pause: str = "Ctrl+Shift+P"
    window_geometry: str = "1120x760"

    @classmethod
    def defaults(cls) -> "AppSettings":
        return cls(output_folder=str(get_videos_folder() / "AeroRecorder"))


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        default_path = (
            portable_data_folder() / "settings.json"
            if is_portable()
            else local_app_data / "AeroRecorder" / "settings.json"
        )
        self.path = path or default_path

    def load(self) -> AppSettings:
        defaults = AppSettings.defaults()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return defaults

        allowed = set(asdict(defaults))
        clean = {key: value for key, value in data.items() if key in allowed}
        merged = {**asdict(defaults), **clean}
        try:
            merged["fps"] = int(merged["fps"])
            merged["countdown_seconds"] = int(merged["countdown_seconds"])
            return AppSettings(**merged)
        except (TypeError, ValueError):
            return defaults

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(asdict(settings), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
