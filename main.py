from __future__ import annotations

import tkinter as tk
import sys

from aero_recorder.ui import AeroRecorderApp
from aero_recorder.winapi import enable_per_monitor_dpi_awareness


def main() -> None:
    enable_per_monitor_dpi_awareness()
    root = tk.Tk()
    if "--smoke-test" in sys.argv:
        root.withdraw()
    AeroRecorderApp(root)
    if "--smoke-test" in sys.argv:
        root.update_idletasks()
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
