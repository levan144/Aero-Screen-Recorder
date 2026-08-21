from __future__ import annotations

import os
import queue
import shutil
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .models import (
    CaptureRegion,
    DisplayMonitor,
    PrivacyMask,
    RecordingMetadata,
    RecordingOptions,
    RecordingResult,
    WindowTarget,
)
from .audio_levels import AudioLevelMonitor
from .countdown import CountdownOverlay
from .encoders import ENCODER_CHOICES
from .hotkeys import Hotkey, HotkeyPoller, focus_allows_hotkeys
from .mouse_effects import MouseEffectsOverlay
from .presets import PRESETS, get_preset
from .recorder import Recorder, find_ffmpeg, list_microphones, list_webcams
from .recordings import (
    create_thumbnail,
    format_duration,
    format_file_size,
    open_recording,
    probe_recording,
    reveal_recording,
    rename_recording,
    scan_recordings,
)
from .region_selector import RegionSelector
from .settings import AppSettings, SettingsStore
from .system_audio import SystemAudioDevice, list_system_audio_devices
from .theme import COLORS, FONT_DISPLAY, FONT_TEXT, FluentButton, ToggleSwitch, create_app_icon
from .tray import SystemTrayIcon
from .updates import UpdateInfo, check_latest_release
from .winapi import (
    apply_windows_11_window_style,
    get_virtual_screen,
    list_display_monitors,
)
from .window_selector import WindowSelector


class RecordingPill:
    def __init__(self, app: "AeroRecorderApp", started_at: float) -> None:
        self.app = app
        self.started_at = started_at
        self.paused_at: float | None = None
        self.paused_total = 0.0
        self.window = tk.Toplevel(app.root)
        self.window.title("AeroRecorder recording")
        self.window.configure(bg=COLORS["surface"])
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.geometry(self._geometry())

        border = tk.Frame(self.window, bg=COLORS["border"], padx=1, pady=1)
        border.pack(fill="both", expand=True)
        content = tk.Frame(border, bg=COLORS["surface"], padx=16, pady=10)
        content.pack(fill="both", expand=True)

        left = tk.Frame(content, bg=COLORS["surface"])
        left.pack(side="left", fill="y")
        dot = tk.Canvas(left, width=14, height=14, bg=COLORS["surface"], highlightthickness=0)
        dot.pack(side="left", padx=(0, 10))
        dot.create_oval(2, 2, 12, 12, fill=COLORS["danger"], outline="")
        self.timer_label = tk.Label(
            left,
            text="00:00",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=(FONT_TEXT, 12, "bold"),
        )
        self.timer_label.pack(side="left")
        self.stop_button = FluentButton(
            content,
            "Stop",
            app.stop_recording,
            danger=True,
            width=84,
            height=38,
            background=COLORS["surface"],
        )
        self.stop_button.pack(side="right")
        self.mute_button = FluentButton(
            content,
            "Mute mic",
            app.toggle_microphone_mute,
            width=92,
            height=38,
            background=COLORS["surface"],
        )
        self.mute_button.pack(side="right", padx=(0, 8))
        options = app.recorder.options
        self.mute_button.set_enabled(bool(options and options.microphone))
        self.pause_button = FluentButton(
            content,
            "Pause",
            app.toggle_pause,
            width=84,
            height=38,
            background=COLORS["surface"],
        )
        self.pause_button.pack(side="right", padx=(0, 8))
        self.window.update_idletasks()
        apply_windows_11_window_style(self.window.winfo_id(), exclude_from_capture=True)
        self._tick()

    def _geometry(self) -> str:
        width, height = 454, 60
        screen_width = self.app.root.winfo_screenwidth()
        return f"{width}x{height}+{screen_width - width - 28}+28"

    def _tick(self) -> None:
        if not self.window.winfo_exists():
            return
        now = time.monotonic()
        active_pause = now - self.paused_at if self.paused_at is not None else 0.0
        elapsed = max(0, int(now - self.started_at - self.paused_total - active_pause))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        value = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        self.timer_label.configure(text=value)
        self.window.after(250, self._tick)

    def set_finishing(self) -> None:
        self.stop_button.set_text("Saving…")
        self.stop_button.set_enabled(False)
        self.pause_button.set_enabled(False)
        self.mute_button.set_enabled(False)

    def set_paused(self, paused: bool) -> None:
        if paused and self.paused_at is None:
            self.paused_at = time.monotonic()
            self.pause_button.set_text("Resume")
        elif not paused and self.paused_at is not None:
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None
            self.pause_button.set_text("Pause")

    def set_microphone_muted(self, muted: bool) -> None:
        self.mute_button.set_text("Unmute mic" if muted else "Mute mic")

    def destroy(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass


class AeroRecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.store = SettingsStore()
        self.settings = self.store.load()
        self.monitors = list_display_monitors()
        self.recorder = Recorder()
        self.audio_meter = AudioLevelMonitor()
        self.selected_region: CaptureRegion | None = None
        self.privacy_masks: list[PrivacyMask] = []
        self.selected_window_title = ""
        self.pill: RecordingPill | None = None
        self.mouse_effects: MouseEffectsOverlay | None = None
        self.recording_started_at = 0.0
        self.current_page = "recorder"
        self.close_after_recording = False
        self.start_pending = False
        self.gif_stop_after_id: str | None = None
        self._microphone_generation = 0
        self._system_audio_generation = 0
        self._preview_generation = 0
        self._webcam_generation = 0
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.tray = SystemTrayIcon(
            lambda: self._ui_queue.put(self.show_from_tray),
            lambda: self._ui_queue.put(self.stop_recording),
            lambda: self._ui_queue.put(self._on_close),
            lambda: self.recorder.is_recording,
        )

        self.root.title("AeroRecorder")
        self.root.configure(bg=COLORS["window"])
        self.root.geometry(self.settings.window_geometry)
        self.root.minsize(960, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_unmap, add="+")
        self.icon = create_app_icon(self.root)
        self.root.iconphoto(True, self.icon)

        self.mode_var = tk.StringVar(value=self.settings.capture_mode)
        self.monitor_var = tk.StringVar()
        self._select_saved_monitor()
        self.fps_var = tk.StringVar(value=str(self.settings.fps))
        self.quality_var = tk.StringVar(value=self.settings.quality)
        self.encoder_var = tk.StringVar(value=self.settings.video_encoder)
        self.output_format_var = tk.StringVar(value=self.settings.output_format)
        self.gif_duration_var = tk.StringVar(value=str(self.settings.gif_duration_seconds))
        self.preset_var = tk.StringVar(value=self.settings.recording_preset)
        self.microphone_var = tk.StringVar(value=self.settings.microphone)
        self.microphone_enabled_var = tk.BooleanVar(value=self.settings.microphone_enabled)
        self.noise_reduction_var = tk.BooleanVar(
            value=self.settings.microphone_noise_reduction
        )
        self.system_audio_var = tk.StringVar(value=self.settings.system_audio_device)
        self.system_audio_enabled_var = tk.BooleanVar(value=self.settings.system_audio_enabled)
        self.microphone_level_var = tk.DoubleVar(value=0.0)
        self.system_audio_level_var = tk.DoubleVar(value=0.0)
        self.webcam_var = tk.StringVar(value=self.settings.webcam)
        self.webcam_enabled_var = tk.BooleanVar(value=self.settings.webcam_enabled)
        self.webcam_shape_var = tk.StringVar(value=self.settings.webcam_shape)
        self.webcam_position_var = tk.StringVar(value=self.settings.webcam_position)
        self.webcam_size_var = tk.StringVar(value=self.settings.webcam_size)
        self.privacy_effect_var = tk.StringVar(value=self.settings.privacy_effect)
        self.cursor_var = tk.BooleanVar(value=self.settings.include_cursor)
        self.mouse_effects_var = tk.BooleanVar(value=self.settings.mouse_effects_enabled)
        self.countdown_var = tk.StringVar(value=str(self.settings.countdown_seconds))
        self.shortcut_record_var = tk.StringVar(value=self.settings.shortcut_record)
        self.shortcut_pause_var = tk.StringVar(value=self.settings.shortcut_pause)
        self.update_check_var = tk.BooleanVar(value=self.settings.check_for_updates)
        self.update_status_var = tk.StringVar(value="Updates are checked through GitHub Releases.")
        self.shortcut_status_var = tk.StringVar(value="Shortcuts work while AeroRecorder is open.")
        self.status_var = tk.StringVar(value="Ready")
        self.region_var = tk.StringVar(value="Choose an area when recording starts")
        self.privacy_status_var = tk.StringVar(value="No privacy masks")

        self._configure_ttk()
        self._build_shell()
        self.hotkeys = HotkeyPoller()
        self._apply_shortcut_bindings()
        self._show_page("recorder")
        self.root.after(60, self._apply_native_style)
        self.root.after(120, self.refresh_microphones)
        self.root.after(160, self.refresh_system_audio_devices)
        self.root.after(200, self.refresh_webcams)
        self.root.after(40, self._drain_ui_queue)
        self.root.after(60, self._poll_hotkeys)
        self.root.after(200, self.tray.start)
        if self.settings.check_for_updates:
            self.root.after(1800, self.check_for_updates)

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        try:
            self.root.after(40, self._drain_ui_queue)
        except tk.TclError:
            pass

    def _configure_ttk(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Aero.TCombobox",
            fieldbackground=COLORS["surface_alt"],
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["text_secondary"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=(10, 8),
            font=(FONT_TEXT, 10),
        )
        style.map(
            "Aero.TCombobox",
            fieldbackground=[("readonly", COLORS["surface_alt"])],
            selectbackground=[("readonly", COLORS["surface_alt"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", COLORS["surface_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", COLORS["accent_text"])
        style.configure(
            "Aero.Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            borderwidth=0,
            rowheight=48,
            font=(FONT_TEXT, 10),
        )
        style.map(
            "Aero.Treeview",
            background=[("selected", "#243746")],
            foreground=[("selected", COLORS["text"])],
        )
        style.configure(
            "Aero.Treeview.Heading",
            background=COLORS["surface_alt"],
            foreground=COLORS["text_secondary"],
            borderwidth=0,
            padding=(12, 10),
            font=(FONT_TEXT, 9, "bold"),
        )
        style.map("Aero.Treeview.Heading", background=[("active", COLORS["surface_alt"])])

    def _poll_hotkeys(self) -> None:
        try:
            if focus_allows_hotkeys(self.root.focus_get):
                self.hotkeys.poll()
            self.root.after(60, self._poll_hotkeys)
        except tk.TclError:
            pass

    def _apply_native_style(self) -> None:
        self.root.update_idletasks()
        apply_windows_11_window_style(self.root.winfo_id())

    def _on_unmap(self, _event: tk.Event) -> None:
        self.root.after_idle(self._hide_if_minimized)

    def _hide_if_minimized(self) -> None:
        try:
            if self.root.state() == "iconic":
                self.root.withdraw()
        except tk.TclError:
            pass

    def show_from_tray(self) -> None:
        if self.recorder.is_recording:
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _build_shell(self) -> None:
        self.sidebar = tk.Frame(self.root, width=220, bg=COLORS["sidebar"])
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=COLORS["sidebar"], padx=22, pady=24)
        brand.pack(fill="x")
        mark = tk.Canvas(brand, width=34, height=34, bg=COLORS["sidebar"], highlightthickness=0)
        mark.pack(side="left")
        mark.create_oval(3, 3, 31, 31, outline=COLORS["accent"], width=3)
        mark.create_oval(11, 11, 23, 23, fill=COLORS["danger"], outline="")
        tk.Label(
            brand,
            text="AeroRecorder",
            bg=COLORS["sidebar"],
            fg=COLORS["text"],
            font=(FONT_DISPLAY, 13, "bold"),
        ).pack(side="left", padx=(10, 0))

        tk.Label(
            self.sidebar,
            text="WORKSPACE",
            bg=COLORS["sidebar"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=24, pady=(12, 8))

        self.nav_buttons: dict[str, tk.Button] = {}
        self._nav_button("recorder", "●", "Recorder")
        self._nav_button("library", "▤", "Recordings")
        self._nav_button("settings", "⚙", "Settings")

        footer = tk.Frame(self.sidebar, bg=COLORS["sidebar"], padx=20, pady=18)
        footer.pack(side="bottom", fill="x")
        self.ffmpeg_dot = tk.Label(
            footer,
            text="●",
            bg=COLORS["sidebar"],
            fg=COLORS["success"] if find_ffmpeg() else COLORS["warning"],
            font=(FONT_TEXT, 9),
        )
        self.ffmpeg_dot.pack(side="left")
        self.ffmpeg_status = tk.Label(
            footer,
            text="FFmpeg ready" if find_ffmpeg() else "FFmpeg needed",
            bg=COLORS["sidebar"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        )
        self.ffmpeg_status.pack(side="left", padx=(7, 0))

        self.content = tk.Frame(self.root, bg=COLORS["window"])
        self.content.pack(side="left", fill="both", expand=True)
        self.pages: dict[str, tk.Frame] = {}
        self.pages["recorder"] = self._build_recorder_page()
        self.pages["library"] = self._build_library_page()
        self.pages["settings"] = self._build_settings_page()

    def _nav_button(self, name: str, icon: str, label: str) -> None:
        button = tk.Button(
            self.sidebar,
            text=f"  {icon}    {label}",
            command=lambda: self._show_page(name),
            bg=COLORS["sidebar"],
            fg=COLORS["text_secondary"],
            activebackground=COLORS["surface_alt"],
            activeforeground=COLORS["text"],
            relief="flat",
            bd=0,
            anchor="w",
            padx=18,
            pady=11,
            cursor="hand2",
            font=(FONT_TEXT, 10),
        )
        button.pack(fill="x", padx=10, pady=2)
        self.nav_buttons[name] = button

    def _show_page(self, name: str) -> None:
        self.current_page = name
        for page_name, page in self.pages.items():
            if page_name == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
            active = page_name == name
            self.nav_buttons[page_name].configure(
                bg=COLORS["surface_alt"] if active else COLORS["sidebar"],
                fg=COLORS["text"] if active else COLORS["text_secondary"],
            )
        if name == "library":
            self.refresh_recordings()

    def _page_header(self, parent: tk.Misc, title: str, subtitle: str) -> tk.Frame:
        header = tk.Frame(parent, bg=COLORS["window"])
        header.pack(fill="x", pady=(0, 22))
        tk.Label(
            header,
            text=title,
            bg=COLORS["window"],
            fg=COLORS["text"],
            font=(FONT_DISPLAY, 24, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            header,
            text=subtitle,
            bg=COLORS["window"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 10),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
        return header

    def _card(self, parent: tk.Misc, *, padding: int = 20) -> tk.Frame:
        border = tk.Frame(parent, bg=COLORS["border"], padx=1, pady=1)
        inner = tk.Frame(border, bg=COLORS["surface"], padx=padding, pady=padding)
        inner.pack(fill="both", expand=True)
        border.inner = inner  # type: ignore[attr-defined]
        return border

    def _section_title(self, parent: tk.Misc, title: str, subtitle: str = "") -> None:
        tk.Label(
            parent,
            text=title,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=(FONT_TEXT, 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        if subtitle:
            tk.Label(
                parent,
                text=subtitle,
                bg=COLORS["surface"],
                fg=COLORS["text_secondary"],
                font=(FONT_TEXT, 9),
                anchor="w",
            ).pack(fill="x", pady=(3, 0))

    def _build_recorder_page(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=COLORS["window"], padx=34, pady=30)
        self._page_header(page, "Screen recorder", "Capture your screen and microphone without the clutter.")

        if not find_ffmpeg():
            self.ffmpeg_banner = tk.Frame(page, bg="#2D281B", padx=16, pady=12)
            self.ffmpeg_banner.pack(fill="x", pady=(0, 16))
            tk.Label(
                self.ffmpeg_banner,
                text="FFmpeg is not installed yet",
                bg="#2D281B",
                fg=COLORS["warning"],
                font=(FONT_TEXT, 10, "bold"),
            ).pack(side="left")
            tk.Label(
                self.ffmpeg_banner,
                text="  Add tools\\ffmpeg.exe to enable recording.",
                bg="#2D281B",
                fg=COLORS["text_secondary"],
                font=(FONT_TEXT, 9),
            ).pack(side="left")
            FluentButton(
                self.ffmpeg_banner,
                "Locate…",
                self.locate_ffmpeg,
                width=88,
                height=34,
                background="#2D281B",
            ).pack(side="right")
        else:
            self.ffmpeg_banner = None

        hero = self._card(page, padding=22)
        hero.pack(fill="x", pady=(0, 16))
        hero_inner = hero.inner  # type: ignore[attr-defined]
        status_icon = tk.Canvas(hero_inner, width=58, height=58, bg=COLORS["surface"], highlightthickness=0)
        status_icon.pack(side="left")
        status_icon.create_oval(2, 2, 56, 56, fill="#202B35", outline=COLORS["border"])
        status_icon.create_oval(19, 19, 39, 39, fill=COLORS["danger"], outline="")
        hero_text = tk.Frame(hero_inner, bg=COLORS["surface"])
        hero_text.pack(side="left", fill="both", expand=True, padx=(16, 12))
        self.hero_title = tk.Label(
            hero_text,
            textvariable=self.status_var,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=(FONT_DISPLAY, 16, "bold"),
            anchor="w",
        )
        self.hero_title.pack(fill="x")
        self.hero_subtitle = tk.Label(
            hero_text,
            text="Press record when you're ready",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
            anchor="w",
        )
        self.hero_subtitle.pack(fill="x", pady=(4, 0))
        self.record_button = FluentButton(
            hero_inner,
            "Start recording",
            self.start_recording,
            accent=True,
            width=156,
            height=44,
            background=COLORS["surface"],
        )
        self.record_button.pack(side="right")
        self.record_button.set_enabled(find_ffmpeg() is not None)

        grid = tk.Frame(page, bg=COLORS["window"])
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure(0, weight=1, uniform="cards")
        grid.grid_columnconfigure(1, weight=1, uniform="cards")

        target = self._card(grid)
        target.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        target_inner = target.inner  # type: ignore[attr-defined]
        self._section_title(target_inner, "Capture target", "Choose everything or select a precise area.")
        modes = tk.Frame(target_inner, bg=COLORS["surface_alt"], padx=4, pady=4)
        modes.pack(fill="x", pady=(16, 12))
        self.mode_buttons: dict[str, tk.Button] = {}
        for mode in ("Full screen", "Monitor", "Area", "Window"):
            button = tk.Button(
                modes,
                text=mode,
                command=lambda value=mode: self._set_mode(value),
                relief="flat",
                bd=0,
                padx=16,
                pady=8,
                cursor="hand2",
                font=(FONT_TEXT, 9, "bold"),
            )
            button.pack(side="left", fill="x", expand=True)
            self.mode_buttons[mode] = button
        self.region_label = tk.Label(
            target_inner,
            textvariable=self.region_var,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8),
            anchor="w",
        )
        self.region_label.pack(fill="x")
        monitor_row = tk.Frame(target_inner, bg=COLORS["surface"])
        monitor_row.pack(fill="x", pady=(8, 0))
        self.monitor_combo = ttk.Combobox(
            monitor_row,
            textvariable=self.monitor_var,
            values=tuple(item.label for item in self.monitors),
            state="readonly",
            style="Aero.TCombobox",
        )
        self.monitor_combo.pack(side="left", fill="x", expand=True)
        self.monitor_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._monitor_changed()
        )
        FluentButton(
            monitor_row,
            "↻",
            self.refresh_monitors,
            width=42,
            height=34,
            background=COLORS["surface"],
            font_size=12,
        ).pack(side="left", padx=(8, 0))
        self._update_mode_buttons()
        privacy_row = tk.Frame(target_inner, bg=COLORS["surface"])
        privacy_row.pack(fill="x", pady=(10, 0))
        self.privacy_effect_combo = ttk.Combobox(
            privacy_row,
            textvariable=self.privacy_effect_var,
            values=("Blur", "Cover"),
            state="readonly",
            width=8,
            style="Aero.TCombobox",
        )
        self.privacy_effect_combo.pack(side="left")
        self.privacy_effect_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._save_settings()
        )
        FluentButton(
            privacy_row,
            "Add mask",
            self.select_privacy_mask,
            width=88,
            height=34,
            background=COLORS["surface"],
        ).pack(side="left", padx=(8, 0))
        FluentButton(
            privacy_row,
            "Clear",
            self.clear_privacy_masks,
            width=66,
            height=34,
            background=COLORS["surface"],
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            privacy_row,
            textvariable=self.privacy_status_var,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8),
        ).pack(side="right")
        delay_row = tk.Frame(target_inner, bg=COLORS["surface"])
        delay_row.pack(fill="x", pady=(12, 0))
        tk.Label(
            delay_row,
            text="Countdown",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        self.countdown_combo = ttk.Combobox(
            delay_row,
            textvariable=self.countdown_var,
            values=("0", "3", "5", "10"),
            state="readonly",
            width=5,
            style="Aero.TCombobox",
        )
        self.countdown_combo.pack(side="right")
        self.countdown_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
        tk.Label(
            delay_row,
            text="seconds",
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8),
        ).pack(side="right", padx=(0, 7))

        audio = self._card(grid)
        audio.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        audio_inner = audio.inner  # type: ignore[attr-defined]
        audio_header = tk.Frame(audio_inner, bg=COLORS["surface"])
        audio_header.pack(fill="x")
        title_group = tk.Frame(audio_header, bg=COLORS["surface"])
        title_group.pack(side="left", fill="x", expand=True)
        self._section_title(title_group, "Audio", "Capture your voice and computer sound.")
        ToggleSwitch(
            audio_header,
            self.microphone_enabled_var,
            self._audio_settings_changed,
            background=COLORS["surface"],
        ).pack(side="right", padx=(10, 0))
        mic_row = tk.Frame(audio_inner, bg=COLORS["surface"])
        mic_row.pack(fill="x", pady=(16, 0))
        self.microphone_combo = ttk.Combobox(
            mic_row,
            textvariable=self.microphone_var,
            state="readonly",
            style="Aero.TCombobox",
            values=(),
        )
        self.microphone_combo.pack(side="left", fill="x", expand=True)
        self.microphone_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._audio_settings_changed()
        )
        self.refresh_mic_button = FluentButton(
            mic_row,
            "↻",
            self.refresh_microphones,
            width=42,
            height=38,
            background=COLORS["surface"],
            font_size=12,
        )
        self.refresh_mic_button.pack(side="left", padx=(8, 0))
        self.microphone_meter = ttk.Progressbar(
            audio_inner,
            variable=self.microphone_level_var,
            maximum=1.0,
            mode="determinate",
        )
        self.microphone_meter.pack(fill="x", pady=(7, 0))

        noise_row = tk.Frame(audio_inner, bg=COLORS["surface"])
        noise_row.pack(fill="x", pady=(10, 0))
        tk.Label(
            noise_row,
            text="Noise reduction",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        ToggleSwitch(
            noise_row,
            self.noise_reduction_var,
            self._save_settings,
            background=COLORS["surface"],
        ).pack(side="right")

        system_header = tk.Frame(audio_inner, bg=COLORS["surface"])
        system_header.pack(fill="x", pady=(15, 0))
        tk.Label(
            system_header,
            text="System audio",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        ToggleSwitch(
            system_header,
            self.system_audio_enabled_var,
            self._audio_settings_changed,
            background=COLORS["surface"],
        ).pack(side="right")

        system_row = tk.Frame(audio_inner, bg=COLORS["surface"])
        system_row.pack(fill="x", pady=(8, 0))
        self.system_audio_combo = ttk.Combobox(
            system_row,
            textvariable=self.system_audio_var,
            state="readonly",
            style="Aero.TCombobox",
            values=(),
        )
        self.system_audio_combo.pack(side="left", fill="x", expand=True)
        self.system_audio_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._audio_settings_changed()
        )
        self.refresh_system_audio_button = FluentButton(
            system_row,
            "↻",
            self.refresh_system_audio_devices,
            width=42,
            height=38,
            background=COLORS["surface"],
            font_size=12,
        )
        self.refresh_system_audio_button.pack(side="left", padx=(8, 0))
        self.system_audio_meter = ttk.Progressbar(
            audio_inner,
            variable=self.system_audio_level_var,
            maximum=1.0,
            mode="determinate",
        )
        self.system_audio_meter.pack(fill="x", pady=(7, 0))

        quality = self._card(grid)
        quality.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(8, 0))
        quality_inner = quality.inner  # type: ignore[attr-defined]
        self._section_title(quality_inner, "Recording quality", "Balanced is ideal for most recordings.")
        preset_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        preset_row.pack(fill="x", pady=(16, 0))
        tk.Label(
            preset_row,
            text="Preset",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        self.preset_combo = ttk.Combobox(
            preset_row,
            textvariable=self.preset_var,
            values=(*PRESETS.keys(), "Custom"),
            state="readonly",
            style="Aero.TCombobox",
            width=18,
        )
        self.preset_combo.pack(side="right")
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _event: self._preset_selected())
        quality_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        quality_row.pack(fill="x", pady=(12, 0))
        self.quality_combo = ttk.Combobox(
            quality_row,
            textvariable=self.quality_var,
            values=("High", "Balanced", "Compact"),
            state="readonly",
            width=14,
            style="Aero.TCombobox",
        )
        self.quality_combo.pack(side="left", fill="x", expand=True)
        self.quality_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._manual_quality_changed()
        )
        self.fps_combo = ttk.Combobox(
            quality_row,
            textvariable=self.fps_var,
            values=("30", "60"),
            state="readonly",
            width=7,
            style="Aero.TCombobox",
        )
        self.fps_combo.pack(side="left", padx=(8, 0))
        self.fps_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._manual_quality_changed()
        )
        tk.Label(
            quality_row,
            text="FPS",
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8, "bold"),
        ).pack(side="left", padx=(6, 0))

        encoder_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        encoder_row.pack(fill="x", pady=(12, 0))
        tk.Label(
            encoder_row,
            text="Video encoder",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        self.encoder_combo = ttk.Combobox(
            encoder_row,
            textvariable=self.encoder_var,
            values=ENCODER_CHOICES,
            state="readonly",
            width=19,
            style="Aero.TCombobox",
        )
        self.encoder_combo.pack(side="right")
        self.encoder_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        format_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        format_row.pack(fill="x", pady=(12, 0))
        tk.Label(
            format_row,
            text="Output format",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        self.output_format_combo = ttk.Combobox(
            format_row,
            textvariable=self.output_format_var,
            values=("MP4", "GIF"),
            state="readonly",
            width=7,
            style="Aero.TCombobox",
        )
        self.output_format_combo.pack(side="right")
        self.output_format_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._format_changed()
        )
        self.gif_duration_combo = ttk.Combobox(
            format_row,
            textvariable=self.gif_duration_var,
            values=("5", "10", "15", "30", "60"),
            state="readonly" if self.output_format_var.get() == "GIF" else "disabled",
            width=5,
            style="Aero.TCombobox",
        )
        self.gif_duration_combo.pack(side="right", padx=(0, 8))
        self.gif_duration_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._save_settings()
        )
        tk.Label(
            format_row,
            text="GIF seconds",
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8),
        ).pack(side="right", padx=(0, 6))

        cursor_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        cursor_row.pack(fill="x", pady=(14, 0))
        tk.Label(
            cursor_row,
            text="Include mouse cursor",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        ToggleSwitch(
            cursor_row,
            self.cursor_var,
            self._manual_quality_changed,
            background=COLORS["surface"],
        ).pack(side="right")

        effects_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        effects_row.pack(fill="x", pady=(12, 0))
        tk.Label(
            effects_row,
            text="Pointer highlight and click rings",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        ToggleSwitch(
            effects_row,
            self.mouse_effects_var,
            self._manual_quality_changed,
            background=COLORS["surface"],
        ).pack(side="right")

        destination = self._card(grid)
        destination.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(8, 0))
        destination_inner = destination.inner  # type: ignore[attr-defined]
        self._section_title(destination_inner, "Save location", "New recordings appear here automatically.")
        self.folder_label = tk.Label(
            destination_inner,
            text=self.settings.output_folder,
            bg=COLORS["surface_alt"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
            anchor="w",
            padx=12,
            pady=10,
        )
        self.folder_label.pack(fill="x", pady=(16, 10))
        actions = tk.Frame(destination_inner, bg=COLORS["surface"])
        actions.pack(fill="x")
        FluentButton(
            actions,
            "Change folder",
            self.choose_output_folder,
            width=126,
            height=36,
            background=COLORS["surface"],
        ).pack(side="left")
        FluentButton(
            actions,
            "Open folder",
            self.open_output_folder,
            width=112,
            height=36,
            background=COLORS["surface"],
        ).pack(side="left", padx=(8, 0))
        return page

    def _build_library_page(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=COLORS["window"], padx=34, pady=30)
        header = self._page_header(page, "Recordings", "Everything you've captured, in one place.")
        actions = tk.Frame(header, bg=COLORS["window"])
        actions.place(relx=1.0, rely=0.15, anchor="ne")
        FluentButton(
            actions,
            "Open folder",
            self.open_output_folder,
            width=112,
            height=36,
            background=COLORS["window"],
        ).pack(side="left")
        FluentButton(
            actions,
            "Refresh",
            self.refresh_recordings,
            width=88,
            height=36,
            background=COLORS["window"],
        ).pack(side="left", padx=(8, 0))

        card = self._card(page, padding=0)
        card.pack(fill="both", expand=True)
        inner = card.inner  # type: ignore[attr-defined]
        list_frame = tk.Frame(inner, bg=COLORS["surface"])
        list_frame.pack(side="left", fill="both", expand=True)
        self.recordings_tree = ttk.Treeview(
            list_frame,
            columns=("name", "date", "size"),
            show="headings",
            style="Aero.Treeview",
            selectmode="browse",
        )
        self.recordings_tree.heading("name", text="NAME")
        self.recordings_tree.heading("date", text="RECORDED")
        self.recordings_tree.heading("size", text="SIZE")
        self.recordings_tree.column("name", minwidth=280, width=460, anchor="w")
        self.recordings_tree.column("date", minwidth=150, width=180, anchor="w")
        self.recordings_tree.column("size", minwidth=80, width=100, anchor="e")
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.recordings_tree.yview
        )
        self.recordings_tree.configure(yscrollcommand=scrollbar.set)
        self.recordings_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.recordings_tree.bind("<Double-1>", lambda _event: self.play_selected())
        self.recordings_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_library_actions())

        preview = tk.Frame(inner, width=350, bg=COLORS["surface_alt"], padx=18, pady=18)
        preview.pack(side="right", fill="y")
        preview.pack_propagate(False)
        self.preview_image_label = tk.Label(
            preview,
            text="Select a recording",
            bg="#101820",
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 10),
            width=40,
            height=11,
            compound="center",
        )
        self.preview_image_label.pack(fill="x")
        self.preview_title = tk.Label(
            preview,
            text="No recording selected",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            font=(FONT_TEXT, 11, "bold"),
            anchor="w",
            wraplength=310,
            justify="left",
        )
        self.preview_title.pack(fill="x", pady=(16, 8))
        self.preview_details = tk.Label(
            preview,
            text="Duration  —\nResolution  —\nRecorded  —\nSize  —",
            bg=COLORS["surface_alt"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 9),
            anchor="nw",
            justify="left",
        )
        self.preview_details.pack(fill="x")
        self.preview_image: tk.PhotoImage | None = None

        bottom = tk.Frame(page, bg=COLORS["window"], pady=14)
        bottom.pack(fill="x")
        self.library_count = tk.Label(
            bottom,
            text="0 recordings",
            bg=COLORS["window"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 9),
        )
        self.library_count.pack(side="left")
        self.delete_button = FluentButton(
            bottom,
            "Delete",
            self.delete_selected,
            danger=True,
            width=82,
            height=36,
            background=COLORS["window"],
        )
        self.delete_button.pack(side="right")
        self.copy_path_button = FluentButton(
            bottom,
            "Copy path",
            self.copy_selected_path,
            width=98,
            height=36,
            background=COLORS["window"],
        )
        self.copy_path_button.pack(side="right", padx=(0, 8))
        self.rename_button = FluentButton(
            bottom,
            "Rename",
            self.rename_selected,
            width=86,
            height=36,
            background=COLORS["window"],
        )
        self.rename_button.pack(side="right", padx=(0, 8))
        self.reveal_button = FluentButton(
            bottom,
            "Show in folder",
            self.reveal_selected,
            width=122,
            height=36,
            background=COLORS["window"],
        )
        self.reveal_button.pack(side="right", padx=(0, 8))
        self.play_button = FluentButton(
            bottom,
            "Play",
            self.play_selected,
            accent=True,
            width=76,
            height=36,
            background=COLORS["window"],
        )
        self.play_button.pack(side="right", padx=(0, 8))
        self._update_library_actions()
        return page

    def _build_settings_page(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=COLORS["window"], padx=34, pady=30)
        self._page_header(page, "Settings", "Customize shortcuts and webcam overlay.")
        card = self._card(page, padding=24)
        card.pack(fill="x")
        inner = card.inner  # type: ignore[attr-defined]
        self._section_title(
            inner,
            "Keyboard shortcuts",
            "Use Ctrl, Alt, Shift, or Win plus a letter, number, function key, Space, Enter, or Esc.",
        )
        fields = (
            ("Start / stop recording", self.shortcut_record_var),
            ("Pause / resume recording", self.shortcut_pause_var),
        )
        for label, variable in fields:
            row = tk.Frame(inner, bg=COLORS["surface"])
            row.pack(fill="x", pady=(18, 0))
            tk.Label(
                row,
                text=label,
                bg=COLORS["surface"],
                fg=COLORS["text_secondary"],
                font=(FONT_TEXT, 10),
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            tk.Entry(
                row,
                textvariable=variable,
                width=24,
                bg=COLORS["surface_alt"],
                fg=COLORS["text"],
                insertbackground=COLORS["text"],
                selectbackground=COLORS["accent"],
                selectforeground=COLORS["accent_text"],
                relief="flat",
                bd=0,
                font=(FONT_TEXT, 10),
            ).pack(side="right", ipady=9, ipadx=10)
        footer = tk.Frame(inner, bg=COLORS["surface"])
        footer.pack(fill="x", pady=(22, 0))
        tk.Label(
            footer,
            textvariable=self.shortcut_status_var,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 9),
        ).pack(side="left")
        FluentButton(
            footer,
            "Save shortcuts",
            self.save_shortcuts,
            accent=True,
            width=132,
            height=38,
            background=COLORS["surface"],
        ).pack(side="right")

        webcam_card = self._card(page, padding=20)
        webcam_card.pack(fill="x", pady=(14, 0))
        webcam_inner = webcam_card.inner  # type: ignore[attr-defined]
        webcam_header = tk.Frame(webcam_inner, bg=COLORS["surface"])
        webcam_header.pack(fill="x")
        webcam_titles = tk.Frame(webcam_header, bg=COLORS["surface"])
        webcam_titles.pack(side="left", fill="x", expand=True)
        self._section_title(
            webcam_titles,
            "Webcam overlay",
            "Place a circular or rectangular camera feed over the recording.",
        )
        ToggleSwitch(
            webcam_header,
            self.webcam_enabled_var,
            self._save_settings,
            background=COLORS["surface"],
        ).pack(side="right", padx=(12, 0))
        camera_row = tk.Frame(webcam_inner, bg=COLORS["surface"])
        camera_row.pack(fill="x", pady=(14, 0))
        self.webcam_combo = ttk.Combobox(
            camera_row,
            textvariable=self.webcam_var,
            values=(),
            state="readonly",
            style="Aero.TCombobox",
        )
        self.webcam_combo.pack(side="left", fill="x", expand=True)
        self.webcam_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
        self.refresh_webcam_button = FluentButton(
            camera_row,
            "↻",
            self.refresh_webcams,
            width=42,
            height=38,
            background=COLORS["surface"],
            font_size=12,
        )
        self.refresh_webcam_button.pack(side="left", padx=(8, 0))
        overlay_row = tk.Frame(webcam_inner, bg=COLORS["surface"])
        overlay_row.pack(fill="x", pady=(10, 0))
        webcam_options = (
            (self.webcam_shape_var, ("Circle", "Rectangle"), 11),
            (self.webcam_size_var, ("Small", "Medium", "Large"), 10),
            (
                self.webcam_position_var,
                ("Top left", "Top right", "Bottom left", "Bottom right"),
                14,
            ),
        )
        for index, (variable, values, width) in enumerate(webcam_options):
            combo = ttk.Combobox(
                overlay_row,
                textvariable=variable,
                values=values,
                state="readonly",
                width=width,
                style="Aero.TCombobox",
            )
            combo.pack(side="left", fill="x", expand=True, padx=(0 if index == 0 else 8, 0))
            combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        update_card = self._card(page, padding=18)
        update_card.pack(fill="x", pady=(14, 0))
        update_inner = update_card.inner  # type: ignore[attr-defined]
        update_text = tk.Frame(update_inner, bg=COLORS["surface"])
        update_text.pack(side="left", fill="x", expand=True)
        tk.Label(
            update_text,
            text="Application updates",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=(FONT_TEXT, 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            update_text,
            textvariable=self.update_status_var,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8),
        ).pack(anchor="w", pady=(3, 0))
        FluentButton(
            update_inner,
            "Check now",
            lambda: self.check_for_updates(manual=True),
            width=96,
            height=36,
            background=COLORS["surface"],
        ).pack(side="right", padx=(12, 0))
        ToggleSwitch(
            update_inner,
            self.update_check_var,
            self._save_settings,
            background=COLORS["surface"],
        ).pack(side="right", padx=(12, 0))
        return page

    def check_for_updates(self, manual: bool = False) -> None:
        self.update_status_var.set("Checking GitHub Releases…")

        def worker() -> None:
            try:
                update = check_latest_release()
                error = ""
            except RuntimeError as exc:
                update, error = None, str(exc)
            self._ui_queue.put(lambda: self._apply_update_check(update, error, manual))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_check(
        self, update: UpdateInfo | None, error: str, manual: bool
    ) -> None:
        if error:
            self.update_status_var.set("Update check unavailable")
            if manual:
                messagebox.showerror("Could not check for updates", error, parent=self.root)
            return
        if update is None:
            self.update_status_var.set("AeroRecorder is up to date")
            if manual:
                messagebox.showinfo(
                    "No update available",
                    "You are using the latest AeroRecorder release.",
                    parent=self.root,
                )
            return
        self.update_status_var.set(f"AeroRecorder {update.version} is available")
        if messagebox.askyesno(
            "AeroRecorder update available",
            f"{update.name} is available. Open the GitHub release page?",
            parent=self.root,
        ) and update.page_url:
            webbrowser.open(update.page_url)

    def _apply_shortcut_bindings(self) -> None:
        try:
            record = Hotkey.parse(self.settings.shortcut_record)
            pause = Hotkey.parse(self.settings.shortcut_pause)
            if record == pause:
                raise ValueError
        except ValueError:
            record = Hotkey.parse("Ctrl+Shift+R")
            pause = Hotkey.parse("Ctrl+Shift+P")
            self.settings.shortcut_record = record.label
            self.settings.shortcut_pause = pause.label
            self.shortcut_record_var.set(record.label)
            self.shortcut_pause_var.set(pause.label)
        self.hotkeys.set_binding("record", record, self._record_shortcut_pressed)
        self.hotkeys.set_binding("pause", pause, self.toggle_pause)

    def save_shortcuts(self) -> None:
        try:
            record = Hotkey.parse(self.shortcut_record_var.get())
            pause = Hotkey.parse(self.shortcut_pause_var.get())
            if record == pause:
                raise ValueError("Start/stop and pause/resume must use different shortcuts.")
        except ValueError as exc:
            self.shortcut_status_var.set(str(exc))
            return
        self.shortcut_record_var.set(record.label)
        self.shortcut_pause_var.set(pause.label)
        self.settings.shortcut_record = record.label
        self.settings.shortcut_pause = pause.label
        self._apply_shortcut_bindings()
        self._save_settings()
        self.shortcut_status_var.set("Shortcuts saved and active.")

    def _record_shortcut_pressed(self) -> None:
        if self.recorder.is_recording:
            self.stop_recording()
        elif not self.start_pending:
            self.start_recording()

    def _set_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        self._update_mode_buttons()
        self._save_settings()

    def _select_saved_monitor(self) -> None:
        selected = next(
            (
                item
                for item in self.monitors
                if item.device == self.settings.monitor_device
            ),
            self.monitors[0] if self.monitors else None,
        )
        self.monitor_var.set(selected.label if selected else "No display found")

    def _selected_monitor(self) -> DisplayMonitor | None:
        label = self.monitor_var.get()
        return next((item for item in self.monitors if item.label == label), None)

    def _monitor_changed(self) -> None:
        monitor = self._selected_monitor()
        if monitor:
            self.settings.monitor_device = monitor.device
        self._update_mode_buttons()
        self._save_settings()

    def refresh_monitors(self) -> None:
        self.monitors = list_display_monitors()
        self.monitor_combo.configure(values=tuple(item.label for item in self.monitors))
        self._select_saved_monitor()
        self._update_mode_buttons()

    def _preset_selected(self) -> None:
        preset = get_preset(self.preset_var.get())
        if preset is None:
            return
        self.quality_var.set(preset.quality)
        self.fps_var.set(str(preset.fps))
        self.cursor_var.set(preset.include_cursor)
        self.mouse_effects_var.set(preset.mouse_effects)
        self._save_settings()

    def _manual_quality_changed(self) -> None:
        self.preset_var.set("Custom")
        self._save_settings()

    def _format_changed(self) -> None:
        self.gif_duration_combo.configure(
            state="readonly" if self.output_format_var.get() == "GIF" else "disabled"
        )
        self._save_settings()

    def _update_mode_buttons(self) -> None:
        selected = self.mode_var.get()
        for mode, button in self.mode_buttons.items():
            active = mode == selected
            button.configure(
                bg=COLORS["accent"] if active else COLORS["surface_alt"],
                fg=COLORS["accent_text"] if active else COLORS["text_secondary"],
                activebackground=COLORS["accent_hover"] if active else COLORS["surface_hover"],
                activeforeground=COLORS["accent_text"] if active else COLORS["text"],
            )
        monitor = self._selected_monitor()
        self.monitor_combo.configure(state="readonly" if selected == "Monitor" else "disabled")
        self.region_var.set(
            "Choose an area when recording starts"
            if selected == "Area" and self.selected_region is None
            else self.selected_region.label
            if selected == "Area" and self.selected_region
            else "Choose a window when recording starts"
            if selected == "Window" and not self.selected_window_title
            else self.selected_window_title
            if selected == "Window"
            else monitor.label
            if selected == "Monitor" and monitor
            else "No display found"
            if selected == "Monitor"
            else "All connected displays will be captured"
        )

    def locate_ffmpeg(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Locate ffmpeg.exe",
            filetypes=[("FFmpeg executable", "ffmpeg.exe"), ("Executable", "*.exe")],
        )
        if not selected:
            return
        destination = Path(__file__).resolve().parent.parent / "tools" / "ffmpeg.exe"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if Path(selected).resolve() != destination.resolve():
                shutil.copy2(selected, destination)
        except OSError as exc:
            messagebox.showerror("Could not add FFmpeg", str(exc), parent=self.root)
            return
        self.recorder.ffmpeg = find_ffmpeg()
        self.ffmpeg_dot.configure(fg=COLORS["success"])
        self.ffmpeg_status.configure(text="FFmpeg ready")
        self.record_button.set_enabled(True)
        if self.ffmpeg_banner:
            self.ffmpeg_banner.destroy()
            self.ffmpeg_banner = None
        self.refresh_microphones()
        self.refresh_webcams()

    def select_privacy_mask(self) -> None:
        if self.recorder.is_recording or self.start_pending:
            return
        self.root.withdraw()
        self.root.after(120, lambda: RegionSelector(self.root, self._privacy_mask_selected))

    def _privacy_mask_selected(self, region: CaptureRegion | None) -> None:
        self.root.deiconify()
        self.root.lift()
        if region is None:
            return
        self.privacy_masks.append(PrivacyMask(region, self.privacy_effect_var.get()))
        count = len(self.privacy_masks)
        self.privacy_status_var.set(f"{count} mask{'s' if count != 1 else ''}")

    def clear_privacy_masks(self) -> None:
        self.privacy_masks.clear()
        self.privacy_status_var.set("No privacy masks")

    def _relative_privacy_masks(
        self, capture_region: CaptureRegion | None
    ) -> tuple[PrivacyMask, ...]:
        if capture_region is None:
            x, y, width, height = get_virtual_screen()
            capture_region = CaptureRegion(x, y, width, height)
        right = capture_region.x + capture_region.width
        bottom = capture_region.y + capture_region.height
        relative: list[PrivacyMask] = []
        for mask in self.privacy_masks:
            left = max(capture_region.x, mask.region.x)
            top = max(capture_region.y, mask.region.y)
            clipped_right = min(right, mask.region.x + mask.region.width)
            clipped_bottom = min(bottom, mask.region.y + mask.region.height)
            if clipped_right - left < 2 or clipped_bottom - top < 2:
                continue
            relative.append(
                PrivacyMask(
                    CaptureRegion(
                        left - capture_region.x,
                        top - capture_region.y,
                        clipped_right - left,
                        clipped_bottom - top,
                    ),
                    mask.effect,
                )
            )
        return tuple(relative)

    def refresh_microphones(self) -> None:
        self._microphone_generation += 1
        generation = self._microphone_generation
        self.microphone_combo.configure(values=("Scanning…",))
        self.microphone_var.set("Scanning…")
        self.refresh_mic_button.set_enabled(False)

        def worker() -> None:
            devices = list_microphones()
            self._ui_queue.put(lambda: self._apply_microphones(generation, devices))

        threading.Thread(target=worker, daemon=True).start()

    def _audio_settings_changed(self) -> None:
        self._save_settings()
        self._restart_audio_meter()

    def _restart_audio_meter(self) -> None:
        if self.recorder.is_recording or self.start_pending:
            self.audio_meter.stop()
            return
        microphone_value = self.microphone_var.get()
        microphone = (
            microphone_value
            if self.microphone_enabled_var.get()
            and microphone_value
            and microphone_value not in {"Scanning…", "No microphone found", "FFmpeg required"}
            else None
        )
        system_value = self.system_audio_var.get()
        system_audio = (
            system_value
            if self.system_audio_enabled_var.get()
            and system_value
            and system_value not in {"Scanning…", "System audio unavailable"}
            else None
        )
        self.audio_meter.start(microphone, system_audio, self._audio_levels_from_thread)

    def _audio_levels_from_thread(self, microphone: float, system_audio: float) -> None:
        self._ui_queue.put(lambda: self._apply_audio_levels(microphone, system_audio))

    def _apply_audio_levels(self, microphone: float, system_audio: float) -> None:
        self.microphone_level_var.set(microphone)
        self.system_audio_level_var.set(system_audio)

    def _apply_microphones(self, generation: int, devices: list[str]) -> None:
        if generation != self._microphone_generation or not self.root.winfo_exists():
            return
        self.refresh_mic_button.set_enabled(True)
        if devices:
            self.microphone_combo.configure(values=devices)
            previous = self.settings.microphone
            self.microphone_var.set(previous if previous in devices else devices[0])
            self.settings.microphone = self.microphone_var.get()
        else:
            message = "No microphone found" if find_ffmpeg() else "FFmpeg required"
            self.microphone_combo.configure(values=(message,))
            self.microphone_var.set(message)
        self._save_settings()
        self._restart_audio_meter()

    def refresh_system_audio_devices(self) -> None:
        self._system_audio_generation += 1
        generation = self._system_audio_generation
        self.system_audio_combo.configure(values=("Scanning…",))
        self.system_audio_var.set("Scanning…")
        self.refresh_system_audio_button.set_enabled(False)

        def worker() -> None:
            try:
                devices = list_system_audio_devices()
            except Exception:
                devices = []
            self._ui_queue.put(lambda: self._apply_system_audio_devices(generation, devices))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_system_audio_devices(
        self, generation: int, devices: list[SystemAudioDevice]
    ) -> None:
        if generation != self._system_audio_generation or not self.root.winfo_exists():
            return
        self.refresh_system_audio_button.set_enabled(True)
        names = [device.name for device in devices]
        if names:
            self.system_audio_combo.configure(values=names)
            previous = self.settings.system_audio_device
            self.system_audio_var.set(previous if previous in names else names[0])
            self.settings.system_audio_device = self.system_audio_var.get()
        else:
            self.system_audio_combo.configure(values=("System audio unavailable",))
            self.system_audio_var.set("System audio unavailable")
            self.system_audio_enabled_var.set(False)
        self._save_settings()
        self._restart_audio_meter()

    def refresh_webcams(self) -> None:
        self._webcam_generation += 1
        generation = self._webcam_generation
        self.webcam_combo.configure(values=("Scanning…",))
        self.webcam_var.set("Scanning…")
        self.refresh_webcam_button.set_enabled(False)

        def worker() -> None:
            devices = list_webcams()
            self._ui_queue.put(lambda: self._apply_webcams(generation, devices))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_webcams(self, generation: int, devices: list[str]) -> None:
        if generation != self._webcam_generation or not self.root.winfo_exists():
            return
        self.refresh_webcam_button.set_enabled(True)
        if devices:
            self.webcam_combo.configure(values=devices)
            previous = self.settings.webcam
            self.webcam_var.set(previous if previous in devices else devices[0])
            self.settings.webcam = self.webcam_var.get()
        else:
            message = "No webcam found" if find_ffmpeg() else "FFmpeg required"
            self.webcam_combo.configure(values=(message,))
            self.webcam_var.set(message)
            self.webcam_enabled_var.set(False)
        self._save_settings()

    def choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose where recordings are saved",
            initialdir=self.settings.output_folder,
        )
        if selected:
            self.settings.output_folder = selected
            self.folder_label.configure(text=selected)
            self._save_settings()
            if self.current_page == "library":
                self.refresh_recordings()

    def open_output_folder(self) -> None:
        folder = Path(self.settings.output_folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self.root)

    def start_recording(self) -> None:
        if self.recorder.is_recording or self.start_pending:
            return
        if not find_ffmpeg():
            messagebox.showwarning(
                "FFmpeg is required",
                "Place ffmpeg.exe in the tools folder or use Locate.",
                parent=self.root,
            )
            return
        self.start_pending = True
        self._save_settings()
        if self.mode_var.get() == "Area":
            self.root.withdraw()
            self.root.after(120, lambda: RegionSelector(self.root, self._region_selected))
        elif self.mode_var.get() == "Window":
            self.root.update_idletasks()
            app_handle = self.root.winfo_id()
            self.root.withdraw()
            self.root.after(
                120,
                lambda: WindowSelector(
                    self.root,
                    self._window_selected,
                    exclude_handle=app_handle,
                ),
            )
        elif self.mode_var.get() == "Monitor":
            monitor = self._selected_monitor()
            if monitor is None:
                self.start_pending = False
                messagebox.showwarning("No display found", "Refresh the display list and try again.", parent=self.root)
                return
            self._start_after_countdown(monitor.region)
        else:
            self._start_after_countdown(None)

    def _region_selected(self, region: CaptureRegion | None) -> None:
        if region is None:
            self.start_pending = False
            self.root.deiconify()
            self.status_var.set("Ready")
            return
        self.selected_region = region
        self.region_var.set(region.label)
        self.root.after(180, lambda: self._start_after_countdown(region))

    def _window_selected(self, target: WindowTarget | None) -> None:
        if target is None:
            self.start_pending = False
            self.root.deiconify()
            self.status_var.set("Ready")
            return
        self.selected_window_title = target.title
        self.region_var.set(target.title)
        self.root.after(180, lambda: self._start_after_countdown(target.region))

    def _start_after_countdown(self, region: CaptureRegion | None) -> None:
        try:
            seconds = int(self.countdown_var.get())
        except ValueError:
            seconds = 3
        if seconds <= 0:
            self._begin_recording(region)
            return
        self.status_var.set("Starting")
        CountdownOverlay(
            self.root,
            seconds,
            lambda: self._begin_recording(region),
            self._countdown_cancelled,
        )

    def _countdown_cancelled(self) -> None:
        self.start_pending = False
        self.root.deiconify()
        self.status_var.set("Ready")

    def _begin_recording(self, region: CaptureRegion | None) -> None:
        self.audio_meter.stop()
        self.microphone_level_var.set(0.0)
        self.system_audio_level_var.set(0.0)
        folder = Path(self.settings.output_folder)
        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        output_format = self.output_format_var.get()
        extension = ".gif" if output_format == "GIF" else ".mp4"
        output = folder / f"Aero Recording {timestamp}{extension}"
        counter = 2
        while output.exists():
            output = folder / f"Aero Recording {timestamp} ({counter}){extension}"
            counter += 1
        microphone = None
        mic_value = self.microphone_var.get()
        if (
            self.microphone_enabled_var.get()
            and mic_value
            and mic_value not in {"Scanning…", "No microphone found", "FFmpeg required"}
        ):
            microphone = mic_value
        system_audio_device = None
        system_audio_value = self.system_audio_var.get()
        if (
            self.system_audio_enabled_var.get()
            and system_audio_value
            and system_audio_value not in {"Scanning…", "System audio unavailable"}
        ):
            system_audio_device = system_audio_value
        webcam = None
        webcam_value = self.webcam_var.get()
        if (
            self.webcam_enabled_var.get()
            and webcam_value
            and webcam_value not in {"Scanning…", "No webcam found", "FFmpeg required"}
        ):
            webcam = webcam_value
        options = RecordingOptions(
            output_path=output,
            fps=int(self.fps_var.get()),
            quality=self.quality_var.get(),
            video_encoder=self.encoder_var.get(),
            output_format=output_format,
            include_cursor=self.cursor_var.get(),
            microphone=microphone,
            microphone_noise_reduction=self.noise_reduction_var.get(),
            system_audio_device=system_audio_device,
            webcam=webcam,
            webcam_shape=self.webcam_shape_var.get(),
            webcam_position=self.webcam_position_var.get(),
            webcam_size=self.webcam_size_var.get(),
            privacy_masks=self._relative_privacy_masks(region),
            region=region,
        )
        try:
            self.recorder.start(options, self._recording_finished_from_thread)
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            self.root.deiconify()
            messagebox.showerror("Could not start recording", str(exc), parent=self.root)
            self.status_var.set("Ready")
            self.start_pending = False
            self._restart_audio_meter()
            return
        self.start_pending = False
        self.status_var.set("Recording")
        self.hero_subtitle.configure(text=output.name)
        self.recording_started_at = time.monotonic()
        self.root.withdraw()
        if self.mouse_effects_var.get():
            self.mouse_effects = MouseEffectsOverlay(self.root)
        self.pill = RecordingPill(self, self.recording_started_at)
        if output_format == "GIF":
            try:
                duration = int(self.gif_duration_var.get())
            except ValueError:
                duration = 15
            self.gif_stop_after_id = self.root.after(
                max(1, duration) * 1000, self._stop_gif_recording
            )

    def _stop_gif_recording(self) -> None:
        self.gif_stop_after_id = None
        if self.recorder.is_recording and self.recorder.options:
            if self.recorder.options.output_format == "GIF":
                self.stop_recording()

    def stop_recording(self) -> None:
        if not self.recorder.is_recording:
            return
        if self.pill:
            self.pill.set_finishing()
        if self.mouse_effects:
            self.mouse_effects.destroy()
            self.mouse_effects = None
        self.recorder.stop()

    def toggle_pause(self) -> None:
        if not self.recorder.is_recording:
            return
        try:
            if self.recorder.is_paused:
                self.recorder.resume()
                self.status_var.set("Recording")
                if self.pill:
                    self.pill.set_paused(False)
            else:
                self.recorder.pause()
                self.status_var.set("Paused")
                if self.pill:
                    self.pill.set_paused(True)
        except OSError as exc:
            messagebox.showerror("Could not pause recording", str(exc), parent=self.root)

    def toggle_microphone_mute(self) -> None:
        if not self.recorder.is_recording:
            return
        muted = not self.recorder.is_microphone_muted
        try:
            self.recorder.set_microphone_muted(muted)
            if self.pill:
                self.pill.set_microphone_muted(muted)
        except OSError as exc:
            messagebox.showerror("Could not mute microphone", str(exc), parent=self.root)

    def _recording_finished_from_thread(self, result: RecordingResult) -> None:
        self._ui_queue.put(lambda: self._recording_finished(result))

    def _recording_finished(self, result: RecordingResult) -> None:
        if self.gif_stop_after_id is not None:
            try:
                self.root.after_cancel(self.gif_stop_after_id)
            except tk.TclError:
                pass
            self.gif_stop_after_id = None
        if self.mouse_effects:
            self.mouse_effects.destroy()
            self.mouse_effects = None
        if self.pill:
            self.pill.destroy()
            self.pill = None
        if self.close_after_recording:
            self._save_settings()
            self.tray.stop()
            self.root.destroy()
            return
        self.root.deiconify()
        self.root.lift()
        if result.success:
            self.status_var.set("Recording saved")
            self.hero_subtitle.configure(text=str(result.output_path))
            self.refresh_recordings()
        else:
            self.status_var.set("Recording failed")
            self.hero_subtitle.configure(text="Check FFmpeg and your recording settings")
            messagebox.showerror(
                "Recording failed",
                result.error or "FFmpeg could not create the recording.",
                parent=self.root,
            )
        self.root.after(3500, self._reset_ready_status)
        self._restart_audio_meter()

    def _reset_ready_status(self) -> None:
        if not self.recorder.is_recording:
            self.status_var.set("Ready")
            self.hero_subtitle.configure(text="Press record when you're ready")

    def refresh_recordings(self) -> None:
        if not hasattr(self, "recordings_tree"):
            return
        for item in self.recordings_tree.get_children():
            self.recordings_tree.delete(item)
        entries = scan_recordings(Path(self.settings.output_folder))
        self.recording_paths: dict[str, Path] = {}
        for index, entry in enumerate(entries):
            item_id = f"recording-{index}"
            self.recording_paths[item_id] = entry.path
            recorded = datetime.fromtimestamp(entry.created_at).strftime("%b %d, %Y  %H:%M")
            self.recordings_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(entry.path.stem, recorded, format_file_size(entry.size_bytes)),
            )
        count = len(entries)
        self.library_count.configure(text=f"{count} recording{'s' if count != 1 else ''}")
        self._update_library_actions()

    def _selected_recording(self) -> Path | None:
        selection = self.recordings_tree.selection()
        return self.recording_paths.get(selection[0]) if selection else None

    def _update_library_actions(self) -> None:
        if not hasattr(self, "play_button"):
            return
        enabled = self._selected_recording() is not None
        self.play_button.set_enabled(enabled)
        self.reveal_button.set_enabled(enabled)
        self.delete_button.set_enabled(enabled)
        self.rename_button.set_enabled(enabled)
        self.copy_path_button.set_enabled(enabled)
        if enabled:
            self._load_recording_preview(self._selected_recording())
        else:
            self._clear_recording_preview()

    def _clear_recording_preview(self) -> None:
        if not hasattr(self, "preview_image_label"):
            return
        self._preview_generation += 1
        self.preview_image = None
        self.preview_image_label.configure(image="", text="Select a recording")
        self.preview_title.configure(text="No recording selected")
        self.preview_details.configure(
            text="Duration  —\nResolution  —\nRecorded  —\nSize  —"
        )

    def _load_recording_preview(self, path: Path | None) -> None:
        if path is None or not hasattr(self, "preview_image_label"):
            return
        self._preview_generation += 1
        generation = self._preview_generation
        self.preview_image = None
        self.preview_image_label.configure(image="", text="Loading preview…")
        self.preview_title.configure(text=path.stem)
        try:
            stat = path.stat()
            recorded = datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y  %H:%M")
            size = format_file_size(stat.st_size)
        except OSError:
            recorded, size = "—", "—"
        self.preview_details.configure(
            text=f"Duration  …\nResolution  …\nRecorded  {recorded}\nSize  {size}"
        )

        def worker() -> None:
            ffmpeg = find_ffmpeg()
            metadata = probe_recording(ffmpeg, path) if ffmpeg else None
            thumbnail = create_thumbnail(ffmpeg, path) if ffmpeg else None
            self._ui_queue.put(
                lambda: self._apply_recording_preview(
                    generation, path, metadata, thumbnail, recorded, size
                )
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_recording_preview(
        self,
        generation: int,
        path: Path,
        metadata: RecordingMetadata | None,
        thumbnail: Path | None,
        recorded: str,
        size: str,
    ) -> None:
        if generation != self._preview_generation or self._selected_recording() != path:
            return
        duration = format_duration(metadata.duration_seconds) if metadata else "—"
        resolution = (
            f"{metadata.width} × {metadata.height}"
            if metadata and metadata.width and metadata.height
            else "—"
        )
        self.preview_details.configure(
            text=(
                f"Duration  {duration}\nResolution  {resolution}\n"
                f"Recorded  {recorded}\nSize  {size}"
            )
        )
        if thumbnail:
            try:
                self.preview_image = tk.PhotoImage(file=str(thumbnail))
                self.preview_image_label.configure(image=self.preview_image, text="")
                return
            except tk.TclError:
                pass
        self.preview_image_label.configure(image="", text="Preview unavailable")

    def play_selected(self) -> None:
        path = self._selected_recording()
        if path:
            try:
                open_recording(path)
            except OSError as exc:
                messagebox.showerror("Could not play recording", str(exc), parent=self.root)

    def reveal_selected(self) -> None:
        path = self._selected_recording()
        if path:
            try:
                reveal_recording(path)
            except OSError as exc:
                messagebox.showerror("Could not open folder", str(exc), parent=self.root)

    def copy_selected_path(self) -> None:
        path = self._selected_recording()
        if not path:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(str(path))
            self.root.update_idletasks()
            self.status_var.set("Recording path copied")
        except tk.TclError as exc:
            messagebox.showerror("Could not copy path", str(exc), parent=self.root)

    def rename_selected(self) -> None:
        path = self._selected_recording()
        if not path:
            return
        requested = simpledialog.askstring(
            "Rename recording",
            "New recording name:",
            initialvalue=path.stem,
            parent=self.root,
        )
        if requested is None:
            return
        try:
            renamed = rename_recording(path, requested)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not rename recording", str(exc), parent=self.root)
            return
        self.refresh_recordings()
        for item_id, item_path in self.recording_paths.items():
            if item_path == renamed:
                self.recordings_tree.selection_set(item_id)
                self.recordings_tree.focus(item_id)
                break

    def delete_selected(self) -> None:
        path = self._selected_recording()
        if not path:
            return
        confirmed = messagebox.askyesno(
            "Delete recording?",
            f"Permanently delete “{path.stem}”?",
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            path.unlink()
        except OSError as exc:
            messagebox.showerror("Could not delete recording", str(exc), parent=self.root)
        self.refresh_recordings()

    def _save_settings(self) -> None:
        self.settings.capture_mode = self.mode_var.get()
        monitor = self._selected_monitor()
        if monitor:
            self.settings.monitor_device = monitor.device
        try:
            self.settings.fps = int(self.fps_var.get())
        except ValueError:
            self.settings.fps = 30
        self.settings.quality = self.quality_var.get()
        self.settings.video_encoder = self.encoder_var.get()
        self.settings.output_format = self.output_format_var.get()
        try:
            self.settings.gif_duration_seconds = int(self.gif_duration_var.get())
        except ValueError:
            self.settings.gif_duration_seconds = 15
        self.settings.recording_preset = self.preset_var.get()
        mic = self.microphone_var.get()
        if mic not in {"Scanning…", "No microphone found", "FFmpeg required"}:
            self.settings.microphone = mic
        self.settings.microphone_enabled = self.microphone_enabled_var.get()
        self.settings.microphone_noise_reduction = self.noise_reduction_var.get()
        system_audio = self.system_audio_var.get()
        if system_audio not in {"Scanning…", "System audio unavailable"}:
            self.settings.system_audio_device = system_audio
        self.settings.system_audio_enabled = self.system_audio_enabled_var.get()
        webcam = self.webcam_var.get()
        if webcam not in {"Scanning…", "No webcam found", "FFmpeg required"}:
            self.settings.webcam = webcam
        self.settings.webcam_enabled = self.webcam_enabled_var.get()
        self.settings.webcam_shape = self.webcam_shape_var.get()
        self.settings.webcam_position = self.webcam_position_var.get()
        self.settings.webcam_size = self.webcam_size_var.get()
        self.settings.privacy_effect = self.privacy_effect_var.get()
        self.settings.include_cursor = self.cursor_var.get()
        self.settings.mouse_effects_enabled = self.mouse_effects_var.get()
        self.settings.shortcut_record = self.shortcut_record_var.get()
        self.settings.shortcut_pause = self.shortcut_pause_var.get()
        self.settings.check_for_updates = self.update_check_var.get()
        try:
            self.settings.countdown_seconds = int(self.countdown_var.get())
        except ValueError:
            self.settings.countdown_seconds = 3
        if self.root.state() == "normal":
            self.settings.window_geometry = self.root.geometry()
        try:
            self.store.save(self.settings)
        except OSError:
            pass

    def _on_close(self) -> None:
        if self.recorder.is_recording:
            confirmed = messagebox.askyesno(
                "Recording in progress",
                "Stop the recording, save it, and exit?",
                parent=self.root,
            )
            if confirmed:
                self.close_after_recording = True
                self.stop_recording()
            return
        self._save_settings()
        self.audio_meter.stop()
        self.tray.stop()
        self.root.destroy()
