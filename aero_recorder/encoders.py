from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ENCODER_CODECS = {
    "Software (CPU)": "libx264",
    "NVIDIA NVENC": "h264_nvenc",
    "AMD AMF": "h264_amf",
    "Intel Quick Sync": "h264_qsv",
}
HARDWARE_ENCODERS = ("NVIDIA NVENC", "AMD AMF", "Intel Quick Sync")
ENCODER_CHOICES = ("Auto", *ENCODER_CODECS)


def parse_encoder_list(output: str) -> set[str]:
    encoders: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"\s*V\S*\s+(\S+)", line)
        if match:
            encoders.add(match.group(1))
    return encoders


@lru_cache(maxsize=4)
def supported_encoders(ffmpeg: Path) -> set[str]:
    try:
        result = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"libx264"}
    return parse_encoder_list(result.stdout + "\n" + result.stderr)


@lru_cache(maxsize=12)
def encoder_works(ffmpeg: Path, codec: str) -> bool:
    if codec not in supported_encoders(ffmpeg):
        return False
    try:
        result = subprocess.run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=128x128:d=0.05",
                "-frames:v",
                "1",
                "-c:v",
                codec,
                "-f",
                "null",
                "NUL" if os.name == "nt" else "/dev/null",
            ],
            capture_output=True,
            timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def resolve_encoder(ffmpeg: Path, requested: str) -> str:
    if requested != "Auto":
        return requested if requested in ENCODER_CODECS else "Software (CPU)"
    for label in HARDWARE_ENCODERS:
        if encoder_works(ffmpeg, ENCODER_CODECS[label]):
            return label
    return "Software (CPU)"


def build_encoder_arguments(encoder: str, quality: str) -> list[str]:
    value = {"High": 18, "Balanced": 23, "Compact": 28}.get(quality, 23)
    codec = ENCODER_CODECS.get(encoder, "libx264")
    if codec == "h264_nvenc":
        return ["-c:v", codec, "-preset", "p4", "-rc", "vbr", "-cq", str(value), "-b:v", "0"]
    if codec == "h264_amf":
        return [
            "-c:v",
            codec,
            "-quality",
            "balanced",
            "-rc",
            "cqp",
            "-qp_i",
            str(value),
            "-qp_p",
            str(value),
        ]
    if codec == "h264_qsv":
        return ["-c:v", codec, "-preset", "veryfast", "-global_quality", str(value)]
    preset = "faster" if quality == "High" else "veryfast"
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(value)]
