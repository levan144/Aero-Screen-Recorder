from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .theme import COLORS, FONT_DISPLAY, FONT_TEXT, FluentButton
from .winapi import apply_windows_11_window_style


class CountdownOverlay:
    def __init__(
        self,
        parent: tk.Misc,
        seconds: int,
        on_finished: Callable[[], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        self.remaining = max(1, seconds)
        self.on_finished = on_finished
        self.on_cancelled = on_cancelled
        self.finished = False

        self.window = tk.Toplevel(parent)
        self.window.title("Recording countdown")
        self.window.configure(bg=COLORS["surface"])
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        width, height = 300, 230
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        self.window.geometry(
            f"{width}x{height}+{(screen_width - width) // 2}+{(screen_height - height) // 2}"
        )
        border = tk.Frame(self.window, bg=COLORS["border"], padx=1, pady=1)
        border.pack(fill="both", expand=True)
        content = tk.Frame(border, bg=COLORS["surface"], padx=28, pady=22)
        content.pack(fill="both", expand=True)
        tk.Label(
            content,
            text="Recording starts in",
            bg=COLORS["surface"],
            fg=COLORS["text_secondary"],
            font=(FONT_TEXT, 11),
        ).pack()
        self.number = tk.Label(
            content,
            text=str(self.remaining),
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            font=(FONT_DISPLAY, 54, "bold"),
        )
        self.number.pack(pady=(4, 8))
        FluentButton(
            content,
            "Cancel",
            self.cancel,
            width=100,
            height=36,
            background=COLORS["surface"],
        ).pack()
        self.window.bind("<Escape>", lambda _event: self.cancel())
        self.window.update_idletasks()
        apply_windows_11_window_style(self.window.winfo_id(), exclude_from_capture=True)
        self.window.after(1000, self._tick)

    def _tick(self) -> None:
        if self.finished:
            return
        self.remaining -= 1
        if self.remaining <= 0:
            self.finished = True
            self.window.destroy()
            self.on_finished()
            return
        self.number.configure(text=str(self.remaining))
        self.window.after(1000, self._tick)

    def cancel(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.window.destroy()
        self.on_cancelled()
