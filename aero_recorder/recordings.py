from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .models import RecordingEntry


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
