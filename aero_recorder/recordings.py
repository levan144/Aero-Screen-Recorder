from __future__ import annotations

import os
import re
import hashlib
import re
import subprocess
from pathlib import Path

from .models import RecordingEntry, RecordingMetadata


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def scan_recordings(folder: Path) -> list[RecordingEntry]:
    try:
        paths = [
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".mp4", ".mkv", ".mov"}
            and ".partial" not in path.name.lower()
        ]
    except OSError:
        return []

    entries: list[RecordingEntry] = []
    for path in paths:
        try:
            stat = path.stat()
            entries.append(RecordingEntry(path, stat.st_mtime, stat.st_size))
        except OSError:
            continue
    return sorted(entries, key=lambda item: item.created_at, reverse=True)


def format_file_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def parse_ffmpeg_metadata(output: str) -> RecordingMetadata:
    duration = 0.0
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width = height = 0
    for line in output.splitlines():
        if "Video:" not in line:
            continue
        dimensions = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", line)
        if dimensions:
            width, height = (int(value) for value in dimensions.groups())
            break
    return RecordingMetadata(duration, width, height)


def probe_recording(ffmpeg: Path, path: Path) -> RecordingMetadata:
    try:
        result = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return RecordingMetadata()
    return parse_ffmpeg_metadata(result.stderr + "\n" + result.stdout)


def create_thumbnail(ffmpeg: Path, path: Path) -> Path | None:
    try:
        fingerprint = f"{path.resolve()}|{path.stat().st_mtime_ns}".encode("utf-8")
    except OSError:
        return None
    cache = path.parent / ".aerorecorder-thumbnails"
    thumbnail = cache / f"{hashlib.sha1(fingerprint).hexdigest()}.png"
    if thumbnail.exists():
        return thumbnail
    try:
        cache.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                str(ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.5",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=300:-2",
                str(thumbnail),
            ],
            capture_output=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return thumbnail if result.returncode == 0 and thumbnail.exists() else None


def open_recording(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_recording(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def rename_recording(path: Path, requested_name: str) -> Path:
    name = requested_name.strip().rstrip(". ")
    if name.lower().endswith(path.suffix.lower()):
        name = name[: -len(path.suffix)].rstrip(". ")
    if not name or re.search(r'[<>:"/\\|?*]', name):
        raise ValueError("Use a file name without < > : \" / \\ | ? or * characters.")
    destination = path.with_name(f"{name}{path.suffix}")
    if destination == path:
        return path
    if destination.exists():
        raise FileExistsError(f"{destination.name} already exists.")
    path.rename(destination)
    return destination
