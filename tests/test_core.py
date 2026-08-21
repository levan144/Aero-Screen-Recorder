from __future__ import annotations

import tempfile
import unittest
import sys
from array import array
from pathlib import Path
from unittest.mock import patch

from aero_recorder.models import (
    CaptureRegion,
    DisplayMonitor,
    PrivacyMask,
    RecordingOptions,
    WindowTarget,
)
from aero_recorder.recorder import (
    build_ffmpeg_command,
    build_gif_command,
    build_video_filter,
    build_webcam_filter,
    find_ffmpeg,
    parse_microphone_devices,
    parse_webcam_devices,
)
from aero_recorder.recordings import (
    format_duration,
    format_file_size,
    parse_ffmpeg_metadata,
    rename_recording,
    scan_recordings,
)
from aero_recorder.settings import AppSettings, SettingsStore
from aero_recorder.runtime import PORTABLE_MARKER, is_portable
from aero_recorder.window_selector import window_at_point
from aero_recorder.hotkeys import Hotkey, focus_allows_hotkeys
from aero_recorder.encoders import build_encoder_arguments, parse_encoder_list
from aero_recorder.mouse_effects import PULSE_DURATION, pulse_radius
from aero_recorder.presets import PRESETS, get_preset
from aero_recorder.updates import is_newer_version, version_tuple
from aero_recorder.audio_levels import best_input_device, pcm_level


class RegionTests(unittest.TestCase):
    def test_monitor_label_contains_resolution_and_primary_state(self) -> None:
        monitor = DisplayMonitor("DISPLAY1", CaptureRegion(-1920, 0, 1920, 1080), True)
        self.assertEqual(monitor.label, "DISPLAY1 — 1920 × 1080 (Primary)")

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
    def test_hardware_encoder_lists_and_arguments_are_supported(self) -> None:
        output = " V..... h264_nvenc NVIDIA NVENC H.264 encoder\n V..... h264_qsv H.264 QSV"
        self.assertEqual(parse_encoder_list(output), {"h264_nvenc", "h264_qsv"})
        nvenc = build_encoder_arguments("NVIDIA NVENC", "High")
        self.assertIn("h264_nvenc", nvenc)
        self.assertIn("18", nvenc)
        qsv = build_encoder_arguments("Intel Quick Sync", "Compact")
        self.assertIn("h264_qsv", qsv)
        self.assertIn("28", qsv)

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
[dshow @ 0001]  "Integrated Camera" (none)
"""
        self.assertEqual(parse_microphone_devices(output), ["USB Microphone", "Headset Mic"])
        self.assertEqual(parse_webcam_devices(output), ["Integrated Camera"])

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
        audio_filter = command[command.index("-af") + 1]
        self.assertIn("asetpts=N/SR/TB", audio_filter)
        self.assertEqual(command[-1], "capture.mp4")

    def test_silent_command_has_no_audio_encoder(self) -> None:
        command = build_ffmpeg_command(
            Path("ffmpeg.exe"), RecordingOptions(Path("capture.mp4"))
        )
        self.assertNotIn("-c:a", command)
        self.assertNotIn("dshow", command)

    def test_gif_conversion_uses_palette_and_looping(self) -> None:
        command = build_gif_command(Path("ffmpeg.exe"), Path("source.mp4"), Path("clip.gif"))
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("palettegen", graph)
        self.assertIn("paletteuse", graph)
        self.assertIn("fps=15", graph)
        self.assertEqual(command[-1], "clip.gif")

    def test_microphone_noise_reduction_uses_ffmpeg_audio_filters(self) -> None:
        command = build_ffmpeg_command(
            Path("ffmpeg.exe"),
            RecordingOptions(
                Path("capture.mp4"),
                microphone="Studio Mic",
                microphone_noise_reduction=True,
            ),
        )
        audio_filter = command[command.index("-af") + 1]
        self.assertIn("highpass=f=100", audio_filter)
        self.assertIn("afftdn=nf=-25", audio_filter)
        self.assertIn("lowpass=f=12000", audio_filter)
        self.assertIn("volume@aeromic=volume=1", audio_filter)

    def test_webcam_is_composited_and_microphone_mapping_is_preserved(self) -> None:
        options = RecordingOptions(
            Path("capture.mp4"),
            microphone="Studio Mic",
            webcam="Integrated Camera",
            webcam_shape="Circle",
            webcam_position="Top right",
            webcam_size="Small",
        )
        command = build_ffmpeg_command(Path("ffmpeg.exe"), options)
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(filter_graph, build_webcam_filter(options, 2))
        self.assertIn("scale=180:180", filter_graph)
        self.assertIn("overlay=W-w-24:24", filter_graph)
        self.assertIn("geq=", filter_graph)
        self.assertIn("[video]", command)
        self.assertIn("1:a:0", command)

    def test_privacy_masks_build_blur_and_cover_filters(self) -> None:
        options = RecordingOptions(
            Path("capture.mp4"),
            privacy_masks=(
                PrivacyMask(CaptureRegion(10, 20, 200, 100), "Blur"),
                PrivacyMask(CaptureRegion(400, 50, 80, 60), "Cover"),
            ),
        )
        graph = build_video_filter(options)
        self.assertIn("crop=200:100:10:20,boxblur=20:2", graph)
        self.assertIn("drawbox=x=400:y=50:w=80:h=60:color=black:t=fill", graph)
        command = build_ffmpeg_command(Path("ffmpeg.exe"), options)
        self.assertIn("-filter_complex", command)
        self.assertIn("[video]", command)


class SettingsTests(unittest.TestCase):
    def test_portable_marker_enables_portable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / PORTABLE_MARKER).write_text("portable", encoding="ascii")
            with patch("aero_recorder.runtime.application_root", return_value=root):
                self.assertTrue(is_portable())

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


class UpdateTests(unittest.TestCase):
    def test_release_versions_are_compared_numerically(self) -> None:
        self.assertEqual(version_tuple("v1.12.3-beta"), (1, 12, 3))
        self.assertTrue(is_newer_version("v0.2.0", "0.1.9"))
        self.assertFalse(is_newer_version("v0.1.0", "0.1.0"))


class AudioLevelTests(unittest.TestCase):
    def test_pcm_level_distinguishes_silence_and_signal(self) -> None:
        self.assertEqual(pcm_level(b"\x00\x00" * 32), 0.0)
        signal = array("h", [12_000, -12_000] * 32).tobytes()
        self.assertGreater(pcm_level(signal), 0.7)

    def test_audio_device_matching_separates_loopback(self) -> None:
        devices = [
            {"index": 1, "name": "Studio Microphone", "maxInputChannels": 1},
            {
                "index": 2,
                "name": "Speakers [Loopback]",
                "maxInputChannels": 2,
                "isLoopbackDevice": True,
            },
        ]
        microphone = best_input_device(devices, "Studio Microphone", loopback=False)
        speakers = best_input_device(devices, "Speakers [Loopback]", loopback=True)
        self.assertEqual(microphone["index"], 1)  # type: ignore[index]
        self.assertEqual(speakers["index"], 2)  # type: ignore[index]


class HotkeyTests(unittest.TestCase):
    def test_hotkey_is_validated_and_normalized(self) -> None:
        self.assertEqual(Hotkey.parse("shift+ctrl+r").label, "Ctrl+Shift+R")
        self.assertEqual(Hotkey.parse("Alt+F12").key_code, 0x7B)

    def test_hotkey_requires_modifier_and_one_key(self) -> None:
        with self.assertRaises(ValueError):
            Hotkey.parse("R")
        with self.assertRaises(ValueError):
            Hotkey.parse("Ctrl+R+P")

    def test_combobox_popdown_focus_does_not_crash_poller(self) -> None:
        def unresolved_popdown() -> object:
            raise KeyError("popdown")

        self.assertFalse(focus_allows_hotkeys(unresolved_popdown))

    def test_text_input_focus_suppresses_global_hotkeys(self) -> None:
        class FocusWidget:
            @staticmethod
            def winfo_class() -> str:
                return "TCombobox"

        self.assertFalse(focus_allows_hotkeys(FocusWidget))
        self.assertTrue(focus_allows_hotkeys(lambda: None))


class MouseEffectsTests(unittest.TestCase):
    def test_click_pulse_expands_and_is_clamped(self) -> None:
        self.assertEqual(pulse_radius(0), 12.0)
        self.assertEqual(pulse_radius(PULSE_DURATION), 44.0)
        self.assertEqual(pulse_radius(PULSE_DURATION * 2), 44.0)


class RecordingPresetTests(unittest.TestCase):
    def test_requested_presets_are_available(self) -> None:
        self.assertEqual(
            set(PRESETS),
            {"Small file", "Balanced", "High quality", "Presentation", "Gaming"},
        )

    def test_gaming_and_presentation_presets_have_expected_behavior(self) -> None:
        gaming = get_preset("Gaming")
        presentation = get_preset("Presentation")
        self.assertIsNotNone(gaming)
        self.assertIsNotNone(presentation)
        self.assertEqual(gaming.fps, 60)  # type: ignore[union-attr]
        self.assertFalse(gaming.include_cursor)  # type: ignore[union-attr]
        self.assertTrue(presentation.mouse_effects)  # type: ignore[union-attr]


class RecordingLibraryTests(unittest.TestCase):
    def test_recording_can_be_renamed_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "Original.mp4"
            original.write_bytes(b"video")
            renamed = rename_recording(original, "New name.mp4")
            self.assertEqual(renamed.name, "New name.mp4")
            self.assertTrue(renamed.exists())
            with self.assertRaises(ValueError):
                rename_recording(renamed, "bad:name")

    def test_scan_filters_partial_and_non_video_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "one.mp4").write_bytes(b"video")
            (folder / "animation.gif").write_bytes(b"gif")
            (folder / "two.partial.mp4").write_bytes(b"partial")
            (folder / "notes.txt").write_text("ignore", encoding="utf-8")
            recordings = scan_recordings(folder)
            self.assertEqual(
                {item.path.name for item in recordings}, {"one.mp4", "animation.gif"}
            )

    def test_file_size_formatting(self) -> None:
        self.assertEqual(format_file_size(512), "512 B")
        self.assertEqual(format_file_size(1536), "1.5 KB")
        self.assertEqual(format_file_size(2 * 1024 * 1024), "2.0 MB")

    def test_video_metadata_is_parsed_and_formatted(self) -> None:
        output = """
Duration: 00:02:03.45, start: 0.000000, bitrate: 2400 kb/s
Stream #0:0: Video: h264, yuv420p, 1920x1080, 30 fps
"""
        metadata = parse_ffmpeg_metadata(output)
        self.assertAlmostEqual(metadata.duration_seconds, 123.45)
        self.assertEqual((metadata.width, metadata.height), (1920, 1080))
        self.assertEqual(format_duration(metadata.duration_seconds), "2:03")


if __name__ == "__main__":
    unittest.main()
