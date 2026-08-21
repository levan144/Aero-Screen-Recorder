from __future__ import annotations

import tkinter as tk
from typing import Callable

from .models import CaptureRegion
from .winapi import get_virtual_screen


class RegionSelector:
    def __init__(
        self,
        parent: tk.Misc,
        on_selected: Callable[[CaptureRegion | None], None],
    ) -> None:
        self.on_selected = on_selected
        self.start_x = 0
        self.start_y = 0
        self.rect_id: int | None = None
        self.label_id: int | None = None
        self.vx, self.vy, self.vw, self.vh = get_virtual_screen()

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.36)
        self.window.configure(bg="#05080D")
        self.window.geometry(f"{self.vw}x{self.vh}{self.vx:+d}{self.vy:+d}")

        self.canvas = tk.Canvas(
            self.window,
            bg="#05080D",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            self.vw // 2,
            52,
            text="Drag to select a recording area  •  Esc to cancel",
            fill="#FFFFFF",
            font=("Segoe UI Variable Display", 16, "bold"),
        )
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.window.bind("<Escape>", lambda _event: self._finish(None))
        self.window.bind("<Button-3>", lambda _event: self._finish(None))

        self.window.deiconify()
        self.window.focus_force()
        self.window.grab_set()

    def _press(self, event: tk.Event) -> None:
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        if self.label_id:
            self.canvas.delete(self.label_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#60CDFF",
            width=4,
            fill="#102A3A",
        )

    def _drag(self, event: tk.Event) -> None:
        if not self.rect_id:
            return
        x = min(max(event.x, 0), self.vw)
        y = min(max(event.y, 0), self.vh)
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, x, y)
        width, height = abs(x - self.start_x), abs(y - self.start_y)
        text = f"{width} × {height}"
        label_x = min(self.start_x, x) + 12
        label_y = min(self.start_y, y) - 16
        if self.label_id:
            self.canvas.itemconfigure(self.label_id, text=text)
            self.canvas.coords(self.label_id, label_x, max(18, label_y))
        else:
            self.label_id = self.canvas.create_text(
                label_x,
                max(18, label_y),
                text=text,
                fill="#FFFFFF",
                anchor="w",
                font=("Segoe UI Variable Text", 11, "bold"),
            )

    def _release(self, event: tk.Event) -> None:
        end_x = min(max(event.x, 0), self.vw)
        end_y = min(max(event.y, 0), self.vh)
        x = min(self.start_x, end_x)
        y = min(self.start_y, end_y)
        width = abs(end_x - self.start_x)
        height = abs(end_y - self.start_y)
        if width < 32 or height < 32:
            self._finish(None)
            return
        region = CaptureRegion(self.vx + x, self.vy + y, width, height)
        self._finish(region.normalized_for_video())

    def _finish(self, region: CaptureRegion | None) -> None:
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        self.on_selected(region)
