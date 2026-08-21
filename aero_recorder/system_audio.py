from __future__ import annotations

import threading
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    import pyaudiowpatch as pyaudio
except ImportError:  # Keep the rest of the app usable in a source-only checkout.
    pyaudio = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class SystemAudioDevice:
    name: str
    index: int
    channels: int
    sample_rate: int


def list_system_audio_devices() -> list[SystemAudioDevice]:
    if pyaudio is None:
        return []
    audio = pyaudio.PyAudio()
    try:
        devices: list[SystemAudioDevice] = []
        for info in audio.get_loopback_device_info_generator():
            channels = max(1, min(2, int(info.get("maxInputChannels", 2))))
            devices.append(
                SystemAudioDevice(
                    name=str(info["name"]),
                    index=int(info["index"]),
                    channels=channels,
                    sample_rate=int(info.get("defaultSampleRate", 48_000)),
                )
            )
        return devices
    finally:
        audio.terminate()


class SystemAudioCapture:
    """Capture a WASAPI loopback device into a temporary PCM wave file."""

    def __init__(self) -> None:
        self._audio = None
        self._stream = None
        self._wave: wave.Wave_write | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.error = ""

    def start(self, device_name: str, output_path: Path) -> None:
        if pyaudio is None:
            raise RuntimeError(
                "System audio support is unavailable. Install PyAudioWPatch and try again."
            )
        device = next(
            (item for item in list_system_audio_devices() if item.name == device_name),
            None,
        )
        if device is None:
            raise RuntimeError("The selected system audio device is no longer available.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        audio = pyaudio.PyAudio()
        try:
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=device.channels,
                rate=device.sample_rate,
                input=True,
                input_device_index=device.index,
                frames_per_buffer=1024,
            )
            wave_file = wave.open(str(output_path), "wb")
            wave_file.setnchannels(device.channels)
            wave_file.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
            wave_file.setframerate(device.sample_rate)
        except Exception:
            audio.terminate()
            raise

        self._audio = audio
        self._stream = stream
        self._wave = wave_file
        self._stop_event.clear()
        self.error = ""
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self) -> None:
        try:
            while not self._stop_event.is_set():
                data = self._stream.read(1024, exception_on_overflow=False)
                self._wave.writeframes(data)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self._close_resources()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._close_resources()

    def _close_resources(self) -> None:
        stream, self._stream = self._stream, None
        wave_file, self._wave = self._wave, None
        audio, self._audio = self._audio, None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if wave_file is not None:
            try:
                wave_file.close()
            except (OSError, wave.Error):
                pass
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass
