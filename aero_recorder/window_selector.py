from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence

from .models import WindowTarget
from .winapi import get_virtual_screen, list_visible_windows


def window_at_point(
    targets: Sequence[WindowTarget], x: int, y: int
) -> WindowTarget | None:
    for target in targets:
        region = target.region
        if region.x <= x < region.x + region.width and region.y <= y < region.y + region.height:
            return target
    return None


class WindowSelector:
    def __init__(
        self,
        parent: tk.Misc,
        on_selected: Callable[[WindowTarget | None], None],
        *,
        exclude_handle: int = 0,
    ) -> None:
        self.on_selected = on_selected
        self.vx, self.vy, self.vw, self.vh = get_virtual_screen()
        self.targets = list_visible_windows(exclude_handle=exclude_handle)
        self.current: WindowTarget | None = None

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.30)
        self.window.configure(bg="#05080D")
        self.window.geometry(f"{self.vw}x{self.vh}{self.vx:+d}{self.vy:+d}")

        self.canvas = tk.Canvas(
            self.window,
            bg="#05080D",
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)
        self.help_id = self.canvas.create_text(
            self.vw // 2,
            52,
            text="Point to a window and click to record it  •  Esc to cancel",
            fill="#FFFFFF",
            font=("Segoe UI Variable Display", 16, "bold"),
        )
        self.highlight_id = self.canvas.create_rectangle(
            0, 0, 0, 0, outline="#60CDFF", width=5, fill="#102A3A", state="hidden"
        )
        self.title_id = self.canvas.create_text(
            0,
            0,
            text="",
            fill="#FFFFFF",
            anchor="sw",
            font=("Segoe UI Variable Text", 11, "bold"),
            state="hidden",
        )
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._select)
        self.window.bind("<Escape>", lambda _event: self._finish(None))
        self.window.bind("<Button-3>", lambda _event: self._finish(None))

        self.window.deiconify()
        self.window.focus_force()
        self.window.grab_set()

    def _motion(self, event: tk.Event) -> None:
        target = window_at_point(self.targets, self.vx + event.x, self.vy + event.y)
        self.current = target
        if target is None:
            self.canvas.itemconfigure(self.highlight_id, state="hidden")
            self.canvas.itemconfigure(self.title_id, state="hidden")
            return
        region = target.region
        left, top = region.x - self.vx, region.y - self.vy
        right, bottom = left + region.width, top + region.height
        self.canvas.coords(self.highlight_id, left, top, right, bottom)
        self.canvas.itemconfigure(self.highlight_id, state="normal")
        self.canvas.coords(self.title_id, left + 10, max(28, top - 8))
        self.canvas.itemconfigure(self.title_id, text=target.title, state="normal")
        self.canvas.tag_raise(self.help_id)

    def _select(self, _event: tk.Event) -> None:
        if self.current is not None:
            self._finish(self.current)

    def _finish(self, target: WindowTarget | None) -> None:
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        self.on_selected(target)
