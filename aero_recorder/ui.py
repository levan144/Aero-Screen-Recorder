from __future__ import annotations

import os
import queue
import shutil
import threading
import time
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .models import CaptureRegion, RecordingOptions, RecordingResult, WindowTarget
from .countdown import CountdownOverlay
from .recorder import Recorder, find_ffmpeg, list_microphones
from .recordings import format_file_size, open_recording, reveal_recording, scan_recordings
from .region_selector import RegionSelector
from .settings import AppSettings, SettingsStore
from .system_audio import SystemAudioDevice, list_system_audio_devices
from .theme import COLORS, FONT_DISPLAY, FONT_TEXT, FluentButton, ToggleSwitch, create_app_icon
from .winapi import apply_windows_11_window_style
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
        width, height = 350, 60
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

    def set_paused(self, paused: bool) -> None:
        if paused and self.paused_at is None:
            self.paused_at = time.monotonic()
            self.pause_button.set_text("Resume")
        elif not paused and self.paused_at is not None:
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None
            self.pause_button.set_text("Pause")

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
        self.recorder = Recorder()
        self.selected_region: CaptureRegion | None = None
        self.selected_window_title = ""
        self.pill: RecordingPill | None = None
        self.recording_started_at = 0.0
        self.current_page = "recorder"
        self.close_after_recording = False
        self._microphone_generation = 0
        self._system_audio_generation = 0
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        self.root.title("AeroRecorder")
        self.root.configure(bg=COLORS["window"])
        self.root.geometry(self.settings.window_geometry)
        self.root.minsize(960, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.icon = create_app_icon(self.root)
        self.root.iconphoto(True, self.icon)

        self.mode_var = tk.StringVar(value=self.settings.capture_mode)
        self.fps_var = tk.StringVar(value=str(self.settings.fps))
        self.quality_var = tk.StringVar(value=self.settings.quality)
        self.microphone_var = tk.StringVar(value=self.settings.microphone)
        self.microphone_enabled_var = tk.BooleanVar(value=self.settings.microphone_enabled)
        self.system_audio_var = tk.StringVar(value=self.settings.system_audio_device)
        self.system_audio_enabled_var = tk.BooleanVar(value=self.settings.system_audio_enabled)
        self.cursor_var = tk.BooleanVar(value=self.settings.include_cursor)
        self.countdown_var = tk.StringVar(value=str(self.settings.countdown_seconds))
        self.status_var = tk.StringVar(value="Ready")
        self.region_var = tk.StringVar(value="Choose an area when recording starts")

        self._configure_ttk()
        self._build_shell()
        self._show_page("recorder")
        self.root.after(60, self._apply_native_style)
        self.root.after(120, self.refresh_microphones)
        self.root.after(160, self.refresh_system_audio_devices)
        self.root.after(40, self._drain_ui_queue)

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

    def _apply_native_style(self) -> None:
        self.root.update_idletasks()
        apply_windows_11_window_style(self.root.winfo_id())

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
        for mode in ("Full screen", "Area", "Window"):
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
        self._update_mode_buttons()
        self.region_label = tk.Label(
            target_inner,
            textvariable=self.region_var,
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8),
            anchor="w",
        )
        self.region_label.pack(fill="x")
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
            self._save_settings,
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
        self.microphone_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
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
            self._save_settings,
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
            "<<ComboboxSelected>>", lambda _event: self._save_settings()
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

        quality = self._card(grid)
        quality.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(8, 0))
        quality_inner = quality.inner  # type: ignore[attr-defined]
        self._section_title(quality_inner, "Recording quality", "Balanced is ideal for most recordings.")
        quality_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        quality_row.pack(fill="x", pady=(16, 0))
        self.quality_combo = ttk.Combobox(
            quality_row,
            textvariable=self.quality_var,
            values=("High", "Balanced", "Compact"),
            state="readonly",
            width=14,
            style="Aero.TCombobox",
        )
        self.quality_combo.pack(side="left", fill="x", expand=True)
        self.quality_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
        self.fps_combo = ttk.Combobox(
            quality_row,
            textvariable=self.fps_var,
            values=("30", "60"),
            state="readonly",
            width=7,
            style="Aero.TCombobox",
        )
        self.fps_combo.pack(side="left", padx=(8, 0))
        self.fps_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
        tk.Label(
            quality_row,
            text="FPS",
            bg=COLORS["surface"],
            fg=COLORS["text_muted"],
            font=(FONT_TEXT, 8, "bold"),
        ).pack(side="left", padx=(6, 0))

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
            self._save_settings,
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
        self.recordings_tree = ttk.Treeview(
            inner,
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
        scrollbar = ttk.Scrollbar(inner, orient="vertical", command=self.recordings_tree.yview)
        self.recordings_tree.configure(yscrollcommand=scrollbar.set)
        self.recordings_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.recordings_tree.bind("<Double-1>", lambda _event: self.play_selected())
        self.recordings_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_library_actions())

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

    def _set_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        self._update_mode_buttons()
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
        self.region_var.set(
            "Choose an area when recording starts"
            if selected == "Area" and self.selected_region is None
            else self.selected_region.label
            if selected == "Area" and self.selected_region
            else "Choose a window when recording starts"
            if selected == "Window" and not self.selected_window_title
            else self.selected_window_title
            if selected == "Window"
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
        if self.recorder.is_recording:
            return
        if not find_ffmpeg():
            messagebox.showwarning(
                "FFmpeg is required",
                "Place ffmpeg.exe in the tools folder or use Locate.",
                parent=self.root,
            )
            return
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
        else:
            self._start_after_countdown(None)

    def _region_selected(self, region: CaptureRegion | None) -> None:
        if region is None:
            self.root.deiconify()
            self.status_var.set("Ready")
            return
        self.selected_region = region
        self.region_var.set(region.label)
        self.root.after(180, lambda: self._start_after_countdown(region))

    def _window_selected(self, target: WindowTarget | None) -> None:
        if target is None:
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
        self.root.deiconify()
        self.status_var.set("Ready")

    def _begin_recording(self, region: CaptureRegion | None) -> None:
        folder = Path(self.settings.output_folder)
        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        output = folder / f"Aero Recording {timestamp}.mp4"
        counter = 2
        while output.exists():
            output = folder / f"Aero Recording {timestamp} ({counter}).mp4"
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
        options = RecordingOptions(
            output_path=output,
            fps=int(self.fps_var.get()),
            quality=self.quality_var.get(),
            include_cursor=self.cursor_var.get(),
            microphone=microphone,
            system_audio_device=system_audio_device,
            region=region,
        )
        try:
            self.recorder.start(options, self._recording_finished_from_thread)
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            self.root.deiconify()
            messagebox.showerror("Could not start recording", str(exc), parent=self.root)
            self.status_var.set("Ready")
            return
        self.status_var.set("Recording")
        self.hero_subtitle.configure(text=output.name)
        self.recording_started_at = time.monotonic()
        self.root.withdraw()
        self.pill = RecordingPill(self, self.recording_started_at)

    def stop_recording(self) -> None:
        if not self.recorder.is_recording:
            return
        if self.pill:
            self.pill.set_finishing()
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

    def _recording_finished_from_thread(self, result: RecordingResult) -> None:
        self._ui_queue.put(lambda: self._recording_finished(result))

    def _recording_finished(self, result: RecordingResult) -> None:
        if self.pill:
            self.pill.destroy()
            self.pill = None
        if self.close_after_recording:
            self._save_settings()
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
        try:
            self.settings.fps = int(self.fps_var.get())
        except ValueError:
            self.settings.fps = 30
        self.settings.quality = self.quality_var.get()
        mic = self.microphone_var.get()
        if mic not in {"Scanning…", "No microphone found", "FFmpeg required"}:
            self.settings.microphone = mic
        self.settings.microphone_enabled = self.microphone_enabled_var.get()
        system_audio = self.system_audio_var.get()
        if system_audio not in {"Scanning…", "System audio unavailable"}:
            self.settings.system_audio_device = system_audio
        self.settings.system_audio_enabled = self.system_audio_enabled_var.get()
        self.settings.include_cursor = self.cursor_var.get()
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
        self.root.destroy()
