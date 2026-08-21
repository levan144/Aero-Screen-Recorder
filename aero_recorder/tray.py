from __future__ import annotations

import ctypes
import os
import sys
import threading
import uuid
from collections.abc import Callable
from ctypes import wintypes


WM_APP = 0x8000
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
TRAY_MESSAGE = WM_APP + 1
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
IDI_APPLICATION = 32512


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "GUID":
        return cls.from_buffer_copy(value.bytes_le)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class SystemTrayIcon:
    """Small dependency-free Windows notification-area icon."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_stop_recording: Callable[[], None],
        on_exit: Callable[[], None],
        is_recording: Callable[[], bool],
    ) -> None:
        self.on_show = on_show
        self.on_stop_recording = on_stop_recording
        self.on_exit = on_exit
        self.is_recording = is_recording
        self._thread: threading.Thread | None = None
        self._window_handle = 0
        self._window_proc = None
        self._notify_data: NOTIFYICONDATAW | None = None
        self._icon_handle = 0
        self._ready = threading.Event()

    def start(self) -> None:
        if os.name != "nt" or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def stop(self) -> None:
        if os.name != "nt":
            return
        handle = self._window_handle
        if handle:
            ctypes.windll.user32.PostMessageW(handle, WM_CLOSE, 0, 0)
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32
        self._configure_functions(user32, kernel32, shell32)
        instance = kernel32.GetModuleHandleW(None)
        class_name = f"AeroRecorderTray_{os.getpid()}"

        @WNDPROC
        def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == TRAY_MESSAGE:
                event = int(lparam) & 0xFFFF
                if event == WM_LBUTTONUP:
                    self.on_show()
                elif event == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                return 0
            if message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                if self._notify_data is not None:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data))
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._window_proc = window_proc
        window_class = WNDCLASSW(
            0,
            window_proc,
            0,
            0,
            instance,
            0,
            0,
            0,
            None,
            class_name,
        )
        atom = user32.RegisterClassW(ctypes.byref(window_class))
        if not atom:
            self._ready.set()
            return
        handle = user32.CreateWindowExW(
            0, class_name, "AeroRecorder tray", 0, 0, 0, 0, 0, 0, 0, instance, None
        )
        if not handle:
            user32.UnregisterClassW(class_name, instance)
            self._ready.set()
            return
        self._window_handle = handle
        self._icon_handle = self._load_icon()
        notify_data = NOTIFYICONDATAW()
        notify_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        notify_data.hWnd = handle
        notify_data.uID = 1
        notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        notify_data.uCallbackMessage = TRAY_MESSAGE
        notify_data.hIcon = self._icon_handle
        notify_data.szTip = "AeroRecorder"
        self._notify_data = notify_data
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data))
        self._ready.set()

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self._window_handle = 0
        user32.UnregisterClassW(class_name, instance)

    @staticmethod
    def _configure_functions(user32: object, kernel32: object, shell32: object) -> None:
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.WORD
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_size_t,
            wintypes.LPCWSTR,
        ]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
        ]
        user32.TrackPopupMenu.restype = wintypes.UINT
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user32.LoadIconW.restype = wintypes.HICON
        shell32.ExtractIconExW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.HICON),
            ctypes.POINTER(wintypes.HICON),
            wintypes.UINT,
        ]
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(NOTIFYICONDATAW),
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    def _load_icon(self) -> int:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        small_icon = wintypes.HICON()
        if shell32.ExtractIconExW(str(sys.executable), 0, None, ctypes.byref(small_icon), 1):
            return int(small_icon.value or 0)
        return int(user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION)) or 0)

    def _show_menu(self, hwnd: int) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, MF_STRING, 1, "Show AeroRecorder")
            if self.is_recording():
                user32.AppendMenuW(menu, MF_STRING, 2, "Stop recording")
            user32.AppendMenuW(menu, MF_STRING, 3, "Exit")
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            if command == 1:
                self.on_show()
            elif command == 2:
                self.on_stop_recording()
            elif command == 3:
                self.on_exit()
        finally:
            user32.DestroyMenu(menu)
