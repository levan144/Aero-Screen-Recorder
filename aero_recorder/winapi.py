from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes
from pathlib import Path

from .models import CaptureRegion, WindowTarget


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
DWMWCP_ROUND = 2
WDA_EXCLUDEFROMCAPTURE = 0x00000011
PROCESS_SUSPEND_RESUME = 0x0800


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "GUID":
        raw = value.bytes_le
        return cls.from_buffer_copy(raw)


def enable_per_monitor_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def apply_windows_11_window_style(hwnd: int, *, exclude_from_capture: bool = False) -> None:
    if os.name != "nt" or not hwnd:
        return
    try:
        enabled = ctypes.c_int(1)
        rounded = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(enabled),
            ctypes.sizeof(enabled),
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(rounded),
            ctypes.sizeof(rounded),
        )
        if exclude_from_capture:
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except (AttributeError, OSError):
        pass


def get_videos_folder() -> Path:
    """Resolve the current user's relocated Windows Videos known folder."""
    fallback = Path.home() / "Videos"
    if os.name != "nt":
        return fallback

    folder_id_videos = GUID.from_uuid(uuid.UUID("18989B1D-99B5-455B-841C-AB7C74E4DDFC"))
    path_ptr = ctypes.c_wchar_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id_videos), 0, None, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            return Path(path_ptr.value)
    except (AttributeError, OSError):
        pass
    finally:
        if path_ptr.value:
            try:
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            except (AttributeError, OSError):
                pass
    return fallback


def get_virtual_screen() -> tuple[int, int, int, int]:
    if os.name != "nt":
        return 0, 0, 1920, 1080
    user32 = ctypes.windll.user32
    return (
        user32.GetSystemMetrics(76),
        user32.GetSystemMetrics(77),
        user32.GetSystemMetrics(78),
        user32.GetSystemMetrics(79),
    )


def list_visible_windows(*, exclude_handle: int = 0) -> list[WindowTarget]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    targets: list[WindowTarget] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        if hwnd == exclude_handle or not user32.IsWindowVisible(hwnd):
            return True
        cloaked = wintypes.DWORD()
        try:
            dwmapi.DwmGetWindowAttribute(
                hwnd,
                DWMWA_CLOAKED,
                ctypes.byref(cloaked),
                ctypes.sizeof(cloaked),
            )
        except (AttributeError, OSError):
            pass
        if cloaked.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        rect = wintypes.RECT()
        result = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result != 0 and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 32 or height < 32:
            return True
        targets.append(
            WindowTarget(
                handle=int(hwnd),
                title=title,
                region=CaptureRegion(rect.left, rect.top, width, height).normalized_for_video(),
            )
        )
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return targets


def set_process_suspended(process_id: int, suspended: bool) -> None:
    """Suspend or resume a process without adding a Windows package dependency."""
    if os.name != "nt":
        raise OSError("Pause and resume are only supported on Windows.")
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, process_id)
    if not handle:
        raise ctypes.WinError()
    try:
        function = (
            ctypes.windll.ntdll.NtSuspendProcess
            if suspended
            else ctypes.windll.ntdll.NtResumeProcess
        )
        status = function(handle)
        if status != 0:
            raise OSError(f"Windows returned status 0x{status & 0xFFFFFFFF:08X}.")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
