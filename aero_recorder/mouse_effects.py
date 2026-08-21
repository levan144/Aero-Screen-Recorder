from __future__ import annotations

import time
import tkinter as tk

from .winapi import get_cursor_position, make_window_click_through, mouse_button_is_down


TRANSPARENT = "#010203"
WINDOW_SIZE = 110
CENTER = WINDOW_SIZE // 2
PULSE_DURATION = 0.45


def pulse_radius(age: float) -> float:
    progress = min(1.0, max(0.0, age / PULSE_DURATION))
    return 12.0 + 32.0 * progress


class MouseEffectsOverlay:
    def __init__(self, parent: tk.Misc) -> None:
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-transparentcolor", TRANSPARENT)
        self.window.configure(bg=TRANSPARENT)
        self.canvas = tk.Canvas(
            self.window,
            width=WINDOW_SIZE,
            height=WINDOW_SIZE,
            bg=TRANSPARENT,
            highlightthickness=0,
        )
        self.canvas.pack()
        self.pulses: list[tuple[float, str]] = []
        self.left_was_down = False
        self.right_was_down = False
        self.running = True
        x, y = get_cursor_position()
        self.window.geometry(f"{WINDOW_SIZE}x{WINDOW_SIZE}{x - CENTER:+d}{y - CENTER:+d}")
        self.window.deiconify()
        self.window.update_idletasks()
        make_window_click_through(self.window.winfo_id())
        self._tick()

    def _tick(self) -> None:
        if not self.running:
            return
        x, y = get_cursor_position()
        self.window.geometry(f"{x - CENTER:+d}{y - CENTER:+d}")
        left_down = mouse_button_is_down("left")
        right_down = mouse_button_is_down("right")
        now = time.monotonic()
        if left_down and not self.left_was_down:
            self.pulses.append((now, "#60CDFF"))
        if right_down and not self.right_was_down:
            self.pulses.append((now, "#FFB900"))
        self.left_was_down = left_down
        self.right_was_down = right_down
        self.pulses = [pulse for pulse in self.pulses if now - pulse[0] <= PULSE_DURATION]
        self._draw(now)
        try:
            self.window.after(16, self._tick)
        except tk.TclError:
            self.running = False

    def _draw(self, now: float) -> None:
        self.canvas.delete("all")
        self.canvas.create_oval(
            CENTER - 18,
            CENTER - 18,
            CENTER + 18,
            CENTER + 18,
            outline="#FFE66D",
            width=3,
        )
        for started_at, color in self.pulses:
            radius = pulse_radius(now - started_at)
            self.canvas.create_oval(
                CENTER - radius,
                CENTER - radius,
                CENTER + radius,
                CENTER + radius,
                outline=color,
                width=4,
            )

    def destroy(self) -> None:
        self.running = False
        try:
            self.window.destroy()
        except tk.TclError:
            pass
