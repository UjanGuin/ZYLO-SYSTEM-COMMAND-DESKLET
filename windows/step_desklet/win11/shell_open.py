"""Open an item from a short-lived request worker and observe the shell handoff."""

import ctypes
import ntpath
import re
import time
from ctypes import wintypes
from enum import Enum

from step_desklet.win11.focus_monitor import _get_user32


class LaunchOutcome(Enum):
    WINDOW_VISIBLE = "window_visible"
    STARTED = "started"
    ACCEPTED = "accepted"
    UNCONFIRMED = "unconfirmed"


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _matches_executable(actual: str, expected: str) -> bool:
    actual = ntpath.normcase(actual)
    expected = ntpath.normcase(expected)
    if actual == expected:
        return True
    directory, name = ntpath.split(actual)
    root, version = ntpath.split(directory)
    # Desktop updaters can start the real app from an app-<version> directory.
    return ntpath.join(root, name) == expected and bool(re.fullmatch(r"app-\d+(?:\.\d+)+.*", version))


class AppWindows:
    def __init__(self, kernel, executable: str, title: str):
        self.kernel = kernel
        self.executable = executable
        self.title = title.casefold()
        self.user = user = _get_user32()
        self.dwm = ctypes.WinDLL("dwmapi")
        self.enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [self.enum_proc, wintypes.LPARAM]
        user.EnumWindows.restype = wintypes.BOOL
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.restype = ctypes.c_int
        user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        user.ShowWindowAsync.restype = wintypes.BOOL
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.SetForegroundWindow.restype = wintypes.BOOL
        self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL

    def _image_path(self, pid: int) -> str:
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            self.kernel.CloseHandle(handle)

    def show(self, pid: int) -> bool:
        if not (pid or self.executable):
            return False
        matches = []
        images = {}

        @self.enum_proc
        def visit(window, _):
            if not self.user.IsWindowVisible(window):
                return True
            caption = ctypes.create_unicode_buffer(512)
            if not self.user.GetWindowTextW(window, caption, len(caption)):
                return True
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if self.executable:
                if owner.value not in images:
                    images[owner.value] = self._image_path(owner.value)
                matched = _matches_executable(images[owner.value], self.executable)
            else:
                matched = owner.value == pid and caption.value.casefold() == self.title
            if matched:
                cloaked = wintypes.DWORD()
                if self.dwm.DwmGetWindowAttribute(window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
                    cloaked.value = 0
                if not cloaked.value:
                    matches.append(window)
            return True

        self.user.EnumWindows(visit, 0)
        if not matches:
            return False
        window = matches[0]
        if self.user.IsIconic(window):
            self.user.ShowWindowAsync(window, 9)  # SW_RESTORE
        self.user.SetForegroundWindow(window)
        return not self.user.IsIconic(window) and bool(self.user.IsWindowVisible(window))


def open_windows_item(target: str, *, app_title: str = "", app_executable: str = "") -> LaunchOutcome:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ole = ctypes.WinDLL("ole32")
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    shell.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.c_void_p,
    ]
    shell.SHParseDisplayName.restype = ctypes.c_long
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    # NOASYNC protects launch handoffs when this worker exits immediately after.
    # NOCLOSEPROCESS lets us detect a newly started process that fails quickly.
    info.fMask = 0x00000100 | 0x00000040 | 0x00000400
    info.lpFile = target
    if ntpath.isabs(target) and target.lower().endswith(".exe"):
        info.lpDirectory = ntpath.dirname(target)
    info.nShow = 1
    item_id = ctypes.c_void_p()
    initialized = ole.CoInitializeEx(None, 0x2 | 0x4)
    if initialized < 0 and initialized & 0xFFFFFFFF != 0x80010106:
        raise OSError(f"Could not initialize the Windows shell (0x{initialized & 0xFFFFFFFF:08x}).")
    try:
        windows = AppWindows(kernel, app_executable, app_title) if app_title else None
        if target.startswith("shell:AppsFolder\\"):
            result = shell.SHParseDisplayName(target, None, ctypes.byref(item_id), 0, None)
            if result < 0 or not item_id.value:
                raise OSError(f"Windows could not resolve the Start menu entry (0x{result & 0xFFFFFFFF:08x}).")
            info.fMask |= 0x0000000C  # SEE_MASK_INVOKEIDLIST, for the item's shell verb.
            info.lpIDList = item_id.value
            info.lpFile = None
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            if windows and windows.show(0):
                return LaunchOutcome.WINDOW_VISIBLE
            raise error
        if windows:
            return _observe_app(kernel, info.hProcess, windows)
        if not info.hProcess:
            return LaunchOutcome.ACCEPTED
        wait = kernel.WaitForSingleObject(info.hProcess, 750)
        if wait == 0x102:  # WAIT_TIMEOUT: a new process is still running.
            return LaunchOutcome.STARTED
        code = wintypes.DWORD()
        if wait == 0 and kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)) and code.value:
            raise OSError(f"The launch process exited with code {code.value}.")
        return LaunchOutcome.ACCEPTED
    finally:
        if info.hProcess:
            kernel.CloseHandle(info.hProcess)
        if item_id.value:
            ole.CoTaskMemFree(item_id)
        if initialized >= 0:
            ole.CoUninitialize()


def _observe_app(kernel, handle, windows: AppWindows) -> LaunchOutcome:
    pid = kernel.GetProcessId(handle) if handle else 0
    deadline = time.monotonic() + 4
    failure = None
    running = False
    while True:
        if windows.show(pid):
            return LaunchOutcome.WINDOW_VISIBLE
        if handle:
            wait = kernel.WaitForSingleObject(handle, 0)
            running = wait == 0x102
            if wait == 0:
                code = wintypes.DWORD()
                if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value:
                    failure = OSError(f"The launch process exited with code {code.value}.")
                pid = 0
                handle = None
        if not (running or pid or windows.executable) or time.monotonic() >= deadline:
            break
        time.sleep(0.15)
    if failure:
        raise failure
    return LaunchOutcome.STARTED if running else LaunchOutcome.ACCEPTED
