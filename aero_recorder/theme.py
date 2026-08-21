from __future__ import annotations

import tkinter as tk
from collections.abc import Callable


COLORS = {
    "window": "#0B0F14",
    "sidebar": "#0E131A",
    "surface": "#151B23",
    "surface_alt": "#1A222C",
    "surface_hover": "#202A35",
    "border": "#28323E",
    "border_soft": "#202A34",
    "text": "#F4F7FA",
    "text_secondary": "#A9B3BF",
    "text_muted": "#778391",
    "accent": "#60CDFF",
    "accent_hover": "#78D6FF",
    "accent_pressed": "#49B8E8",
    "accent_text": "#061018",
    "danger": "#FF6B6B",
    "danger_hover": "#FF8585",
    "success": "#6CCB8E",
    "warning": "#F6C85F",
}

FONT_DISPLAY = "Segoe UI Variable Display"
FONT_TEXT = "Segoe UI Variable Text"
FONT_SYMBOL = "Segoe Fluent Icons"


def rounded_rectangle(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
    radius = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class FluentButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        accent: bool = False,
        danger: bool = False,
        width: int = 150,
        height: int = 42,
        background: str | None = None,
        font_size: int = 10,
    ) -> None:
        self.width_value = width
        self.height_value = height
        self.command = command
        self.text_value = text
        self.accent = accent
        self.danger = danger
        self.enabled = True
        self.parent_background = background or COLORS["surface"]
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=self.parent_background,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.bind("<Enter>", lambda _event: self._draw("hover"))
        self.bind("<Leave>", lambda _event: self._draw("normal"))
        self.bind("<ButtonPress-1>", lambda _event: self._draw("pressed"))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw("normal")

    def _palette(self, state: str) -> tuple[str, str, str]:
        if not self.enabled:
            return COLORS["surface_alt"], COLORS["border_soft"], COLORS["text_muted"]
        if self.danger:
            fill = COLORS["danger_hover"] if state == "hover" else COLORS["danger"]
            if state == "pressed":
                fill = "#E85D5D"
            return fill, fill, "#1A0707"
        if self.accent:
            fill = {
                "normal": COLORS["accent"],
                "hover": COLORS["accent_hover"],
                "pressed": COLORS["accent_pressed"],
            }.get(state, COLORS["accent"])
            return fill, fill, COLORS["accent_text"]
        fill = COLORS["surface_hover"] if state == "hover" else COLORS["surface_alt"]
        if state == "pressed":
            fill = COLORS["border"]
        return fill, COLORS["border"], COLORS["text"]

    def _draw(self, state: str) -> None:
        self.delete("all")
        fill, outline, text_color = self._palette(state)
        rounded_rectangle(
            self,
            1,
            1,
            self.width_value - 1,
            self.height_value - 1,
            9,
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_text(
            self.width_value // 2,
            self.height_value // 2,
            text=self.text_value,
            fill=text_color,
            font=(FONT_TEXT, 10, "bold"),
        )

    def _release(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self.width_value and 0 <= event.y <= self.height_value
        self._draw("hover" if inside else "normal")
        if inside and self.enabled:
            self.command()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw("normal")

    def set_text(self, text: str) -> None:
        self.text_value = text
        self._draw("normal")


class ToggleSwitch(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
        *,
        background: str | None = None,
    ) -> None:
        self.variable = variable
        self.command = command
        self.parent_background = background or COLORS["surface"]
        super().__init__(
            parent,
            width=42,
            height=24,
            bg=self.parent_background,
            highlightthickness=0,
            cursor="hand2",
        )
        self.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_args: self._draw())
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        active = self.variable.get()
        fill = COLORS["accent"] if active else COLORS["border"]
        rounded_rectangle(self, 1, 2, 41, 22, 10, fill=fill, outline=fill)
        center = 30 if active else 12
        self.create_oval(center - 7, 5, center + 7, 19, fill="#FFFFFF", outline="")

    def _toggle(self, _event: tk.Event) -> None:
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()


def create_app_icon(master: tk.Misc) -> tk.PhotoImage:
    image = tk.PhotoImage(master=master, width=32, height=32)
    image.put(COLORS["window"], to=(0, 0, 32, 32))
    for y in range(4, 28):
        for x in range(4, 28):
            distance = ((x - 15.5) ** 2 + (y - 15.5) ** 2) ** 0.5
            if 10.0 <= distance <= 12.0:
                image.put(COLORS["accent"], (x, y))
            elif distance < 5.5:
                image.put(COLORS["danger"], (x, y))
    return image
