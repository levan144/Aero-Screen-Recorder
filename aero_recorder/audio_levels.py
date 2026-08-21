from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
from array import array
from collections.abc import Callable, Iterable
from typing import Any

from .runtime import application_root


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def pcm_level(data: bytes) -> float:
    if len(data) < 2:
        return 0.0
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    if rms <= 0:
        return 0.0
    decibels = 20.0 * math.log10(min(1.0, rms / 32768.0))
    return max(0.0, min(1.0, (decibels + 60.0) / 60.0))


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def best_input_device(
    devices: Iterable[dict[str, Any]], requested: str, *, loopback: bool
) -> dict[str, Any] | None:
    requested_name = _normalized_name(requested)
    candidates = [
        info
        for info in devices
        if int(info.get("maxInputChannels", 0)) > 0
        and bool(info.get("isLoopbackDevice", False)) == loopback
    ]
    if not candidates:
        return None

    def score(info: dict[str, Any]) -> tuple[int, int]:
        name = _normalized_name(str(info.get("name", "")))
        exact = int(name == requested_name)
        overlap = len(requested_name) if requested_name and requested_name in name else 0
        return exact, overlap

    selected = max(candidates, key=score)
    return selected if score(selected) != (0, 0) or not requested_name else None


class AudioLevelMonitor:
    """Read live levels from a crash-isolated PyAudio helper process."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    def start(
        self,
        microphone: str | None,
        system_audio: str | None,
        callback: Callable[[float, float], None],
    ) -> None:
        self.stop()
        if not microphone and not system_audio:
            callback(0.0, 0.0)
            return
        payload = json.dumps({"microphone": microphone, "system_audio": system_audio})
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--audio-meter-worker", payload]
        else:
            command = [
                sys.executable,
                str(application_root() / "main.py"),
                "--audio-meter-worker",
                payload,
            ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="ascii",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            callback(0.0, 0.0)
            return
        self._process = process
        self._thread = threading.Thread(
            target=self._read_levels,
            args=(process, callback),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        process, self._process = self._process, None
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @staticmethod
    def _read_levels(
        process: subprocess.Popen[str],
        callback: Callable[[float, float], None],
    ) -> None:
        if not process.stdout:
            return
        for line in process.stdout:
            try:
                microphone, system_audio = (float(value) for value in line.split(",", 1))
            except ValueError:
                continue
            callback(microphone, system_audio)


def run_audio_meter_worker(payload: str) -> int:
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return 1
    try:
        requested = json.loads(payload)
    except json.JSONDecodeError:
        return 2
    microphone = requested.get("microphone")
    system_audio = requested.get("system_audio")
    audio = pyaudio.PyAudio()
    streams: list[tuple[str, Any]] = []
    try:
        devices = [
            audio.get_device_info_by_index(index)
            for index in range(audio.get_device_count())
        ]
        for kind, device_name, loopback in (
            ("microphone", microphone, False),
            ("system", system_audio, True),
        ):
            if not device_name:
                continue
            device = best_input_device(devices, str(device_name), loopback=loopback)
            if device is None:
                continue
            channels = max(1, min(2, int(device.get("maxInputChannels", 1))))
            rate = int(device.get("defaultSampleRate", 48_000))
            try:
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=int(device["index"]),
                    frames_per_buffer=1024,
                )
            except Exception:
                continue
            streams.append((kind, stream))
        while streams:
            levels = {"microphone": 0.0, "system": 0.0}
            for kind, stream in streams:
                try:
                    levels[kind] = pcm_level(
                        stream.read(1024, exception_on_overflow=False)
                    )
                except Exception:
                    levels[kind] = 0.0
            print(f"{levels['microphone']:.4f},{levels['system']:.4f}", flush=True)
    except (BrokenPipeError, OSError):
        pass
    finally:
        for _kind, stream in streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        audio.terminate()
    return 0
