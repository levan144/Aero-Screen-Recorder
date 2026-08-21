from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .models import RecordingOptions, RecordingResult
from .system_audio import SystemAudioCapture


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_ffmpeg() -> Path | None:
    root = application_root()
    candidates = [
        root / "tools" / "ffmpeg.exe",
        root / "ffmpeg.exe",
    ]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(0, Path(bundle_root) / "tools" / "ffmpeg.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    located = shutil.which("ffmpeg")
    return Path(located) if located else None


def parse_microphone_devices(output: str) -> list[str]:
    devices: list[str] = []
    pattern = re.compile(r'"(?P<name>.+?)"\s+\(audio\)\s*$')
    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            name = match.group("name")
            if name not in devices:
                devices.append(name)
    return devices


def list_microphones(ffmpeg: Path | None = None) -> list[str]:
    binary = ffmpeg or find_ffmpeg()
    if not binary:
        return []
    try:
        result = subprocess.run(
            [
                str(binary),
                "-hide_banner",
                "-list_devices",
                "true",
                "-f",
                "dshow",
                "-i",
                "dummy",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_microphone_devices(result.stderr + "\n" + result.stdout)


def build_ffmpeg_command(ffmpeg: Path, options: RecordingOptions) -> list[str]:
    quality = {
        "High": (18, "faster"),
        "Balanced": (23, "veryfast"),
        "Compact": (28, "veryfast"),
    }
    crf, preset = quality.get(options.quality, quality["Balanced"])

    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-thread_queue_size",
        "1024",
        "-f",
        "gdigrab",
        "-framerate",
        str(options.fps),
        "-draw_mouse",
        "1" if options.include_cursor else "0",
    ]
    if options.region:
        region = options.region.normalized_for_video()
        command.extend(
            [
                "-offset_x",
                str(region.x),
                "-offset_y",
                str(region.y),
                "-video_size",
                f"{region.width}x{region.height}",
            ]
        )
    command.extend(["-i", "desktop"])

    if options.microphone:
        command.extend(
            [
                "-thread_queue_size",
                "1024",
                "-f",
                "dshow",
                "-i",
                f"audio={options.microphone}",
            ]
        )

    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if options.microphone:
        command.extend(
            [
                "-af",
                "aresample=async=1:first_pts=0",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar",
                "48000",
            ]
        )
    command.extend(["-movflags", "+faststart", str(options.output_path)])
    return command


class Recorder:
    def __init__(self) -> None:
        self.ffmpeg = find_ffmpeg()
        self.process: subprocess.Popen[str] | None = None
        self.options: RecordingOptions | None = None
        self.final_output_path: Path | None = None
        self._finish_callback: Callable[[RecordingResult], None] | None = None
        self._lock = threading.Lock()
        self._stopping = False
        self._system_audio: SystemAudioCapture | None = None
        self._system_audio_path: Path | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self.process is not None and self.process.poll() is None

    def start(
        self,
        options: RecordingOptions,
        on_finish: Callable[[RecordingResult], None],
    ) -> None:
        if self.is_recording:
            raise RuntimeError("A recording is already in progress.")
        self.ffmpeg = find_ffmpeg()
        if not self.ffmpeg:
            raise FileNotFoundError(
                "FFmpeg was not found. Place ffmpeg.exe in the tools folder."
            )

        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = options.output_path.with_name(
            f"{options.output_path.stem}.partial{options.output_path.suffix}"
        )
        temporary_options = replace(options, output_path=temporary_path)
        command = build_ffmpeg_command(self.ffmpeg, temporary_options)
        system_audio: SystemAudioCapture | None = None
        system_audio_path: Path | None = None
        if options.system_audio_device:
            system_audio_path = temporary_path.with_suffix(".system.wav")
            system_audio = SystemAudioCapture()
            try:
                system_audio.start(options.system_audio_device, system_audio_path)
            except Exception as exc:
                raise RuntimeError(f"Could not capture system audio: {exc}") from exc
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            if system_audio:
                system_audio.stop()
            raise RuntimeError(f"Could not start FFmpeg: {exc}") from exc

        with self._lock:
            self.process = process
            self.options = temporary_options
            self.final_output_path = options.output_path
            self._finish_callback = on_finish
            self._stopping = False
            self._system_audio = system_audio
            self._system_audio_path = system_audio_path
        threading.Thread(target=self._monitor, daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            process = self.process
            system_audio = self._system_audio
            if not process or process.poll() is not None or self._stopping:
                return
            self._stopping = True
        if system_audio:
            system_audio.stop()
        try:
            if process.stdin:
                process.stdin.write("q\n")
                process.stdin.flush()
                process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        threading.Thread(target=self._force_stop_if_needed, args=(process,), daemon=True).start()

    @staticmethod
    def _force_stop_if_needed(process: subprocess.Popen[str]) -> None:
        try:
            process.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def _monitor(self) -> None:
        with self._lock:
            process = self.process
            options = self.options
            final_output_path = self.final_output_path
            system_audio = self._system_audio
            system_audio_path = self._system_audio_path
        if not process or not options or not final_output_path:
            return

        error_lines: list[str] = []
        try:
            if process.stderr:
                for line in process.stderr:
                    clean = line.strip()
                    if clean:
                        error_lines.append(clean)
                        if len(error_lines) > 40:
                            error_lines.pop(0)
            return_code = process.wait()
        except OSError as exc:
            error_lines.append(str(exc))
            return_code = process.returncode if process.returncode is not None else -1
        if system_audio:
            system_audio.stop()
        success = return_code == 0 and options.output_path.exists()
        error = ""
        if success:
            try:
                if system_audio_path:
                    self._merge_system_audio(
                        options,
                        system_audio_path,
                        final_output_path,
                        error_lines,
                    )
                else:
                    options.output_path.replace(final_output_path)
            except OSError as exc:
                success = False
                error_lines.append(f"Could not finalize recording: {exc}")
            except RuntimeError as exc:
                success = False
                error_lines.append(str(exc))
        if not success:
            error = "\n".join(error_lines[-8:]) or f"FFmpeg exited with code {return_code}."
            try:
                if options.output_path.exists():
                    options.output_path.unlink()
            except OSError:
                pass
        if system_audio_path:
            try:
                system_audio_path.unlink(missing_ok=True)
            except OSError:
                pass

        result = RecordingResult(final_output_path, success, error)
        with self._lock:
            callback = self._finish_callback
            self.process = None
            self.options = None
            self.final_output_path = None
            self._finish_callback = None
            self._stopping = False
            self._system_audio = None
            self._system_audio_path = None
        if callback:
            callback(result)

    def _merge_system_audio(
        self,
        options: RecordingOptions,
        audio_path: Path,
        final_output_path: Path,
        error_lines: list[str],
    ) -> None:
        if not self.ffmpeg or not audio_path.exists() or audio_path.stat().st_size <= 44:
            raise RuntimeError("System audio capture did not produce any audio data.")
        merged_path = final_output_path.with_name(
            f"{final_output_path.stem}.merged{final_output_path.suffix}"
        )
        command = [
            str(self.ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(options.output_path),
            "-i",
            str(audio_path),
        ]
        if options.microphone:
            command.extend(
                [
                    "-filter_complex",
                    "[0:a:0][1:a:0]amix=inputs=2:duration=longest:dropout_transition=2[a]",
                    "-map",
                    "0:v:0",
                    "-map",
                    "[a]",
                ]
            )
        else:
            command.extend(["-map", "0:v:0", "-map", "1:a:0"])
        command.extend(
            [
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-shortest",
                "-movflags",
                "+faststart",
                str(merged_path),
            ]
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not merged_path.exists():
            error_lines.extend(result.stderr.strip().splitlines()[-8:])
            try:
                merged_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("FFmpeg could not combine the system audio and video.")
        merged_path.replace(final_output_path)
        options.output_path.unlink(missing_ok=True)
