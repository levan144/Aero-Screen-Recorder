from __future__ import annotations

import math
import re
import threading
from array import array
from collections.abc import Callable, Iterable
from typing import Any

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None  # type: ignore[assignment]


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
    def __init__(self) -> None:
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(
        self,
        microphone: str | None,
        system_audio: str | None,
        callback: Callable[[float, float], None],
    ) -> None:
        self.stop()
        if pyaudio is None or (not microphone and not system_audio):
            callback(0.0, 0.0)
            return
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._monitor,
            args=(stop_event, microphone, system_audio, callback),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        stop_event, self._stop_event = self._stop_event, None
        if stop_event:
            stop_event.set()
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _monitor(
        self,
        stop_event: threading.Event,
        microphone: str | None,
        system_audio: str | None,
        callback: Callable[[float, float], None],
    ) -> None:
        audio = pyaudio.PyAudio()
        streams: list[tuple[str, Any]] = []
        try:
            devices = [
                audio.get_device_info_by_index(index)
                for index in range(audio.get_device_count())
            ]
            for kind, requested, loopback in (
                ("microphone", microphone, False),
                ("system", system_audio, True),
            ):
                if not requested:
                    continue
                device = best_input_device(devices, requested, loopback=loopback)
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
            while not stop_event.is_set() and streams:
                levels = {"microphone": 0.0, "system": 0.0}
                for kind, stream in streams:
                    try:
                        levels[kind] = pcm_level(
                            stream.read(1024, exception_on_overflow=False)
                        )
                    except Exception:
                        levels[kind] = 0.0
                callback(levels["microphone"], levels["system"])
        finally:
            for _kind, stream in streams:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            audio.terminate()
