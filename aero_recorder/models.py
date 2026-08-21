from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True, slots=True)
class CaptureRegion:
    x: int
    y: int
    width: int
    height: int

    def normalized_for_video(self) -> "CaptureRegion":
        """Return an H.264-friendly region with positive, even dimensions."""
        width = max(2, self.width - (self.width % 2))
        height = max(2, self.height - (self.height % 2))
        return CaptureRegion(self.x, self.y, width, height)

    @property
    def label(self) -> str:
        return f"{self.width} x {self.height} at {self.x}, {self.y}"


@dataclass(frozen=True, slots=True)
class WindowTarget:
    handle: int
    title: str
    region: CaptureRegion


@dataclass(frozen=True, slots=True)
class DisplayMonitor:
    device: str
    region: CaptureRegion
    primary: bool = False

    @property
    def label(self) -> str:
        suffix = " (Primary)" if self.primary else ""
        return f"{self.device} — {self.region.width} × {self.region.height}{suffix}"


@dataclass(frozen=True, slots=True)
class PrivacyMask:
    region: CaptureRegion
    effect: str = "Blur"


@dataclass(frozen=True, slots=True)
class RecordingOptions:
    output_path: Path
    fps: int = 30
    quality: str = "Balanced"
    video_encoder: str = "Auto"
    output_format: str = "MP4"
    include_cursor: bool = True
    microphone: Optional[str] = None
    microphone_noise_reduction: bool = False
    system_audio_device: Optional[str] = None
    webcam: Optional[str] = None
    webcam_shape: str = "Circle"
    webcam_position: str = "Bottom right"
    webcam_size: str = "Medium"
    privacy_masks: tuple[PrivacyMask, ...] = ()
    region: Optional[CaptureRegion] = None


@dataclass(frozen=True, slots=True)
class RecordingEntry:
    path: Path
    created_at: float
    size_bytes: int


@dataclass(frozen=True, slots=True)
class RecordingMetadata:
    duration_seconds: float = 0.0
    width: int = 0
    height: int = 0


@dataclass(frozen=True, slots=True)
class RecordingResult:
    output_path: Path
    success: bool
    error: str = ""
