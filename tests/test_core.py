from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from aero_recorder.models import CaptureRegion, RecordingOptions, WindowTarget
from aero_recorder.recorder import build_ffmpeg_command, find_ffmpeg, parse_microphone_devices
from aero_recorder.recordings import format_file_size, scan_recordings
from aero_recorder.settings import AppSettings, SettingsStore
from aero_recorder.window_selector import window_at_point


class RegionTests(unittest.TestCase):
    def test_region_is_normalized_to_even_dimensions(self) -> None:
        region = CaptureRegion(-100, 25, 101, 99).normalized_for_video()
        self.assertEqual(region, CaptureRegion(-100, 25, 100, 98))

    def test_topmost_window_at_point_is_selected(self) -> None:
        back = WindowTarget(1, "Back", CaptureRegion(0, 0, 800, 600))
        front = WindowTarget(2, "Front", CaptureRegion(50, 50, 200, 100))
        self.assertEqual(window_at_point([front, back], 75, 75), front)
        self.assertEqual(window_at_point([front, back], 700, 500), back)
        self.assertIsNone(window_at_point([front, back], 900, 700))


class RecorderCommandTests(unittest.TestCase):
    def test_ffmpeg_is_found_inside_packaged_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "tools" / "ffmpeg.exe"
            binary.parent.mkdir()
            binary.write_bytes(b"portable")
            with patch.object(sys, "_MEIPASS", temp, create=True):
                self.assertEqual(find_ffmpeg(), binary)

    def test_microphones_are_parsed_and_deduplicated(self) -> None:
        output = """
[dshow @ 0001]  "USB Microphone" (audio)
[dshow @ 0001]     Alternative name "@device_cm_..."
[dshow @ 0001]  "USB Microphone" (audio)
[dshow @ 0001]  "Headset Mic" (audio)
"""
        self.assertEqual(parse_microphone_devices(output), ["USB Microphone", "Headset Mic"])

    def test_region_and_microphone_are_added_to_command(self) -> None:
        command = build_ffmpeg_command(
            Path("ffmpeg.exe"),
            RecordingOptions(
                Path("capture.mp4"),
                fps=60,
                microphone="USB Microphone",
                region=CaptureRegion(10, 20, 801, 601),
            ),
        )
        self.assertIn("800x600", command)
        self.assertIn("audio=USB Microphone", command)
        self.assertIn("60", command)
        self.assertIn("setpts=N/60/TB", command)
        self.assertIn("asetpts=N/SR/TB", command)
        self.assertEqual(command[-1], "capture.mp4")

    def test_silent_command_has_no_audio_encoder(self) -> None:
        command = build_ffmpeg_command(
            Path("ffmpeg.exe"), RecordingOptions(Path("capture.mp4"))
        )
        self.assertNotIn("-c:a", command)
        self.assertNotIn("dshow", command)


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = SettingsStore(path)
            expected = AppSettings(
                output_folder=str(Path(temp) / "Videos"),
                capture_mode="Area",
                fps=60,
                microphone="Studio Mic",
                countdown_seconds=5,
            )
            store.save(expected)
            actual = store.load()
            self.assertEqual(actual.output_folder, expected.output_folder)
            self.assertEqual(actual.capture_mode, "Area")
            self.assertEqual(actual.fps, 60)
            self.assertEqual(actual.microphone, "Studio Mic")
            self.assertEqual(actual.countdown_seconds, 5)

    def test_corrupt_settings_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            loaded = SettingsStore(path).load()
            self.assertEqual(loaded.capture_mode, "Full screen")
            self.assertEqual(loaded.fps, 30)


class RecordingLibraryTests(unittest.TestCase):
    def test_scan_filters_partial_and_non_video_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "one.mp4").write_bytes(b"video")
            (folder / "two.partial.mp4").write_bytes(b"partial")
            (folder / "notes.txt").write_text("ignore", encoding="utf-8")
            recordings = scan_recordings(folder)
            self.assertEqual([item.path.name for item in recordings], ["one.mp4"])

    def test_file_size_formatting(self) -> None:
        self.assertEqual(format_file_size(512), "512 B")
        self.assertEqual(format_file_size(1536), "1.5 KB")
        self.assertEqual(format_file_size(2 * 1024 * 1024), "2.0 MB")


if __name__ == "__main__":
    unittest.main()
