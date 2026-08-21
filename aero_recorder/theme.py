from __future__ import annotations

import tkinter as tk
from collections.abc import Callable


COLORS = {
    "window": "#080B12",
    "sidebar": "#0B1019",
    "surface": "#111824",
    "surface_alt": "#172131",
    "surface_lifted": "#1B2636",
    "surface_hover": "#202E40",
    "border": "#29384D",
    "border_soft": "#1D2939",
    "text": "#F5F8FC",
    "text_secondary": "#AAB7C8",
    "text_muted": "#718096",
    "accent": "#5CE1E6",
    "accent_hover": "#7DE9ED",
    "accent_pressed": "#42C9CF",
    "accent_soft": "#15323B",
    "accent_text": "#041315",
    "violet": "#8B7CFF",
    "violet_soft": "#211F3E",
    "danger": "#FF5878",
    "danger_hover": "#FF7892",
    "success": "#62E6A7",
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
        self.font_size = font_size
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
            11,
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_text(
            self.width_value // 2,
            self.height_value // 2,
            text=self.text_value,
            fill=text_color,
            font=(FONT_TEXT, self.font_size, "bold"),
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


class CaptureModeButton(tk.Canvas):
    """Compact, dependency-free capture target tile with a drawn thematic icon."""

    def __init__(
        self,
        parent: tk.Misc,
        mode: str,
        subtitle: str,
        command: Callable[[], None],
        *,
        width: int = 138,
        height: int = 74,
        background: str | None = None,
    ) -> None:
        self.mode = mode
        self.subtitle = subtitle
        self.command = command
        self.width_value = width
        self.height_value = height
        self.selected = False
        self.hovered = False
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
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _enter(self, _event: tk.Event) -> None:
        self.hovered = True
        self._draw()

    def _leave(self, _event: tk.Event) -> None:
        self.hovered = False
        self._draw()

    def _release(self, event: tk.Event) -> None:
        if 0 <= event.x <= self.width_value and 0 <= event.y <= self.height_value:
            self.command()

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._draw()

    def _draw_icon(self, color: str) -> None:
        x, y = 22, 25
        if self.mode == "Full screen":
            for points in (
                (x - 8, y - 8, x - 2, y - 8, x - 8, y - 2),
                (x + 8, y - 8, x + 2, y - 8, x + 8, y - 2),
                (x - 8, y + 8, x - 2, y + 8, x - 8, y + 2),
                (x + 8, y + 8, x + 2, y + 8, x + 8, y + 2),
            ):
                self.create_line(*points, fill=color, width=2, capstyle="round")
        elif self.mode == "Monitor":
            self.create_rectangle(x - 10, y - 8, x + 10, y + 5, outline=color, width=2)
            self.create_line(x, y + 5, x, y + 10, fill=color, width=2)
            self.create_line(x - 6, y + 10, x + 6, y + 10, fill=color, width=2)
        elif self.mode == "Area":
            self.create_rectangle(
                x - 9,
                y - 9,
                x + 9,
                y + 9,
                outline=color,
                width=2,
                dash=(3, 2),
            )
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline="")
        else:
            self.create_rectangle(x - 9, y - 7, x + 6, y + 7, outline=color, width=2)
            self.create_rectangle(x - 4, y - 10, x + 10, y + 4, outline=color, width=1)

    def _draw(self) -> None:
        self.delete("all")
        if self.selected:
            fill, border, icon = COLORS["accent_soft"], COLORS["accent"], COLORS["accent"]
        elif self.hovered:
            fill, border, icon = COLORS["surface_hover"], COLORS["border"], COLORS["text"]
        else:
            fill, border, icon = COLORS["surface_alt"], COLORS["border_soft"], COLORS["text_secondary"]
        rounded_rectangle(
            self,
            1,
            1,
            self.width_value - 1,
            self.height_value - 1,
            12,
            fill=fill,
            outline=border,
            width=1,
        )
        self._draw_icon(icon)
        self.create_text(
            43,
            22,
            text=self.mode,
            fill=COLORS["text"],
            anchor="w",
            font=(FONT_TEXT, 9, "bold"),
        )
        self.create_text(
            43,
            43,
            text=self.subtitle,
            fill=COLORS["text_muted"],
            anchor="w",
            font=(FONT_TEXT, 7),
        )
        if self.selected:
            self.create_oval(
                self.width_value - 17,
                11,
                self.width_value - 9,
                19,
                fill=COLORS["accent"],
                outline="",
            )


class SignalMeter(tk.Canvas):
    """A low-profile segmented signal meter bound to a Tk numeric variable."""

    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.DoubleVar,
        *,
        background: str | None = None,
        height: int = 8,
    ) -> None:
        self.variable = variable
        self.parent_background = background or COLORS["surface"]
        self._rectangles: list[int] = []
        self._last_active = -1
        self._update_after_id: str | None = None
        super().__init__(
            parent,
            height=height,
            bg=self.parent_background,
            highlightthickness=0,
            bd=0,
        )
        self.bind("<Configure>", lambda _event: self._layout_segments())
        self.variable.trace_add("write", lambda *_args: self._schedule_update())

    def _layout_segments(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(6, self.winfo_height())
        segments = 24
        gap = 2
        segment_width = max(2, (width - gap * (segments - 1)) / segments)
        self._rectangles = []
        for index in range(segments):
            x1 = index * (segment_width + gap)
            x2 = min(width, x1 + segment_width)
            self._rectangles.append(
                self.create_rectangle(
                    x1,
                    1,
                    x2,
                    height - 1,
                    fill=COLORS["border_soft"],
                    outline="",
                )
            )
        self._last_active = -1
        self._apply_level()

    def _schedule_update(self) -> None:
        if self._update_after_id is None:
            self._update_after_id = self.after(40, self._apply_level)

    def _apply_level(self) -> None:
        self._update_after_id = None
        if not self._rectangles:
            return
        try:
            active = round(
                max(0.0, min(1.0, float(self.variable.get()))) * len(self._rectangles)
            )
        except (tk.TclError, ValueError):
            active = 0
        if active == self._last_active:
            return
        for index, rectangle in enumerate(self._rectangles):
            if index < active:
                color = COLORS["warning"] if index >= 20 else COLORS["accent"]
            else:
                color = COLORS["border_soft"]
            self.itemconfigure(rectangle, fill=color)
        self._last_active = active


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
