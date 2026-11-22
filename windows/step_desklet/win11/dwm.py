"""Windows 11 26H2 DWM & SetWindowCompositionAttribute Acrylic glass and Win32 helpers."""

import ctypes
import re
import sys
from ctypes import wintypes
from functools import lru_cache

IS_WINDOWS = sys.platform == "win32"

DWMWA_EXCLUDED_FROM_PEEK = 12
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_NONE = 0xFFFFFFFE

DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2

WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOPMOST = 0x00000008
GW_HWNDPREV = 3
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

LONG_PTR = ctypes.c_ssize_t
HRESULT = ctypes.c_long

class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint32),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def configure_win32_prototypes(user32=None, dwmapi=None, kernel32=None) -> None:
    """Explicitly configure 64-bit safe argtypes and restypes for all Win32/DWM APIs."""
    if user32 is not None:
        set_wca = getattr(user32, "SetWindowCompositionAttribute", None)
        if set_wca is not None:
            set_wca.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
            set_wca.restype = wintypes.BOOL

        set_win_pos = getattr(user32, "SetWindowPos", None)
        if set_win_pos is not None:
            set_win_pos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            set_win_pos.restype = wintypes.BOOL

        get_window = getattr(user32, "GetWindow", None)
        if get_window is not None:
            get_window.argtypes = [wintypes.HWND, wintypes.UINT]
            get_window.restype = wintypes.HWND

        is_window = getattr(user32, "IsWindow", None)
        if is_window is not None:
            is_window.argtypes = [wintypes.HWND]
            is_window.restype = wintypes.BOOL

        for fn_name in ("GetWindowLongPtrW", "GetWindowLongW"):
            fn = getattr(user32, fn_name, None)
            if fn is not None:
                fn.argtypes = [wintypes.HWND, ctypes.c_int]
                fn.restype = LONG_PTR

        for fn_name in ("SetWindowLongPtrW", "SetWindowLongW"):
            fn = getattr(user32, fn_name, None)
            if fn is not None:
                fn.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
                fn.restype = LONG_PTR

    if dwmapi is not None:
        dwm_set = getattr(dwmapi, "DwmSetWindowAttribute", None)
        if dwm_set is not None:
            dwm_set.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            dwm_set.restype = HRESULT

    if kernel32 is not None:
        create_mutex = getattr(kernel32, "CreateMutexW", None)
        if create_mutex is not None:
            create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            create_mutex.restype = wintypes.HANDLE

        create_event = getattr(kernel32, "CreateEventW", None)
        if create_event is not None:
            create_event.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            create_event.restype = wintypes.HANDLE

        set_event = getattr(kernel32, "SetEvent", None)
        if set_event is not None:
            set_event.argtypes = [wintypes.HANDLE]
            set_event.restype = wintypes.BOOL

        wait = getattr(kernel32, "WaitForSingleObject", None)
        if wait is not None:
            wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            wait.restype = wintypes.DWORD

        close_handle = getattr(kernel32, "CloseHandle", None)
        if close_handle is not None:
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL

        get_last_error = getattr(kernel32, "GetLastError", None)
        if get_last_error is not None:
            get_last_error.argtypes = []
            get_last_error.restype = wintypes.DWORD


@lru_cache(maxsize=1)
def _get_win32_libs():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    configure_win32_prototypes(user32=user32, dwmapi=dwmapi, kernel32=kernel32)
    return user32, dwmapi, kernel32


def _hex_to_abgr(hex_color: str, alpha: int = 0xD8) -> int:
    """Convert #RRGGBB to Win32 0xAABBGGRR format for SetWindowCompositionAttribute."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_color or ""):
        hex_color = "#1b132b"
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return ((alpha & 0xFF) << 24) | (b << 16) | (g << 8) | r


def _hex_to_colorref(hex_color: str) -> int:
    """Convert #RRGGBB to Win32 COLORREF (0x00BBGGRR) for DWMWA_BORDER_COLOR."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_color or ""):
        hex_color = "#a78bfa"
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return (b << 16) | (g << 8) | r


def apply_win11_acrylic(
    hwnd: int,
    accent_hex: str = "#a78bfa",
    hide_dwm_border: bool = False,
    *,
    enable_blur: bool = True,
) -> bool:
    """Configure DWM for native acrylic or for a custom transparent window surface."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        user32, dwmapi, _ = _get_win32_libs()
        hwnd_ptr = wintypes.HWND(hwnd)

        set_wca = getattr(user32, "SetWindowCompositionAttribute", None)
        wca_ok = not enable_blur
        if enable_blur and set_wca is not None:
            accent = ACCENT_POLICY()
            accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
            accent.AccentFlags = 2
            accent.GradientColor = _hex_to_abgr("#1b132b", alpha=0xD8)

            data = WINDOWCOMPOSITIONATTRIBDATA()
            data.Attribute = WCA_ACCENT_POLICY
            data.Data = ctypes.pointer(accent)
            data.SizeOfData = ctypes.sizeof(accent)
            wca_ok = bool(set_wca(hwnd_ptr, ctypes.byref(data)))

        dark_mode = wintypes.DWORD(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd_ptr, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode)
        )

        corner_pref = wintypes.DWORD(DWMWCP_ROUND if enable_blur else DWMWCP_DONOTROUND)
        dwmapi.DwmSetWindowAttribute(
            hwnd_ptr, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner_pref), ctypes.sizeof(corner_pref)
        )

        border_val = DWMWA_COLOR_NONE if hide_dwm_border else _hex_to_colorref(accent_hex)
        border_color = wintypes.DWORD(border_val)
        dwmapi.DwmSetWindowAttribute(
            hwnd_ptr, DWMWA_BORDER_COLOR, ctypes.byref(border_color), ctypes.sizeof(border_color)
        )

        exclude_peek = wintypes.DWORD(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd_ptr, DWMWA_EXCLUDED_FROM_PEEK, ctypes.byref(exclude_peek), ctypes.sizeof(exclude_peek)
        )

        return wca_ok
    except Exception:
        return False


def apply_tool_window_exstyle(hwnd: int) -> None:
    """Hide desklet from Windows Taskbar and Alt+Tab switcher."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        user32, _, _ = _get_win32_libs()
        hwnd_ptr = wintypes.HWND(hwnd)
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_long(hwnd_ptr, GWL_EXSTYLE))
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        set_long(hwnd_ptr, GWL_EXSTYLE, LONG_PTR(style))
    except Exception:
        pass


def show_window_above_wallpaper(hwnd: int, wallpaper: int) -> None:
    """Place the desklet just above the wallpaper in normal window order."""
    user32, _, _ = _get_win32_libs()
    previous = user32.GetWindow(wallpaper, GW_HWNDPREV)
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    if previous == hwnd:
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags | SWP_NOZORDER)
        return
    if not user32.IsWindow(wallpaper):
        return
    # A topmost predecessor must never promote the desklet into that band.
    if not previous or user32.GetWindowLongPtrW(previous, GWL_EXSTYLE) & WS_EX_TOPMOST:
        previous = HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, previous, 0, 0, 0, 0, flags)


def acquire_single_instance_mutex(name: str = "Local\\ZYLODesktopAssistantWin11"):
    """Acquire a Win32 named mutex; returns handle or None if another instance exists."""
    if not IS_WINDOWS:
        return True
    try:
        _, _, kernel32 = _get_win32_libs()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, name)
        last_err = ctypes.get_last_error()
        ERROR_ALREADY_EXISTS = 183
        if not handle:
            return None
        if last_err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
            return None
        return handle
    except Exception:
        return True


def close_win32_handle(handle) -> None:
    if not IS_WINDOWS or not handle or handle is True:
        return
    try:
        _, _, kernel32 = _get_win32_libs()
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        pass


def release_single_instance_mutex(handle) -> None:
    """Close a Win32 mutex handle acquired via acquire_single_instance_mutex."""
    close_win32_handle(handle)


def open_settings_request_event(name: str = "Local\\ZYLODesktopAssistantSettings"):
    """Open a session-local auto-reset event for reopening Settings."""
    _, _, kernel32 = _get_win32_libs()
    handle = kernel32.CreateEventW(None, False, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def signal_settings_request(handle) -> None:
    _, _, kernel32 = _get_win32_libs()
    if not kernel32.SetEvent(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def take_settings_request(handle) -> bool:
    _, _, kernel32 = _get_win32_libs()
    wait_object_0 = 0
    return kernel32.WaitForSingleObject(handle, 0) == wait_object_0
