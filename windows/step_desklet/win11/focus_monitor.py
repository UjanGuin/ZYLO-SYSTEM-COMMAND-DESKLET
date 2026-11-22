"""Win32 EVENT_SYSTEM_FOREGROUND hook & desktop focus monitor for Windows 11 26H2."""

import ctypes
import os
import sys
from ctypes import wintypes
from functools import lru_cache
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

IS_WINDOWS = sys.platform == "win32"

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZEEND = 0x0017
WINEVENT_OUTOFCONTEXT = 0x0000
GA_ROOT = 2
WALLPAPER_WINDOW_CLASSES = {"Progman", "WorkerW"}

_CALLBACK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WINEVENTPROC = _CALLBACK_FACTORY(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)

# On Windows 11 24H2/26H2, clicking the wallpaper or pressing Win+D transfers
# foreground focus to Progman, WorkerW, or Shell_TrayWnd (taskbar).
DESKTOP_WINDOW_CLASSES = WALLPAPER_WINDOW_CLASSES | {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland",
}


def configure_focus_prototypes(user32) -> None:
    """Configure explicit 64-bit safe Win32 prototypes for foreground & WinEvent hooks."""
    if user32 is None:
        return

    get_fg = getattr(user32, "GetForegroundWindow", None)
    if get_fg is not None:
        get_fg.argtypes = []
        get_fg.restype = wintypes.HWND

    for name in ("GetShellWindow", "GetDesktopWindow"):
        get_window = getattr(user32, name, None)
        if get_window is not None:
            get_window.argtypes = []
            get_window.restype = wintypes.HWND

    get_ancestor = getattr(user32, "GetAncestor", None)
    if get_ancestor is not None:
        get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        get_ancestor.restype = wintypes.HWND

    get_rect = getattr(user32, "GetWindowRect", None)
    if get_rect is not None:
        get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        get_rect.restype = wintypes.BOOL

    window_from_point = getattr(user32, "WindowFromPoint", None)
    if window_from_point is not None:
        window_from_point.argtypes = [wintypes.POINT]
        window_from_point.restype = wintypes.HWND

    get_cls = getattr(user32, "GetClassNameW", None)
    if get_cls is not None:
        get_cls.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        get_cls.restype = ctypes.c_int

    get_pid = getattr(user32, "GetWindowThreadProcessId", None)
    if get_pid is not None:
        get_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        get_pid.restype = wintypes.DWORD

    set_hook = getattr(user32, "SetWinEventHook", None)
    if set_hook is not None:
        set_hook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            WINEVENTPROC,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        set_hook.restype = wintypes.HANDLE

    unhook = getattr(user32, "UnhookWinEvent", None)
    if unhook is not None:
        unhook.argtypes = [wintypes.HANDLE]
        unhook.restype = wintypes.BOOL


@lru_cache(maxsize=1)
def _get_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    configure_focus_prototypes(user32)
    return user32


def _get_window_class(user32, hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, len(buf))
    return buf.value


def _get_foreground_info() -> tuple[int, str, int]:
    if not IS_WINDOWS:
        return 0, "Progman", os.getpid()
    user32 = _get_user32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, _get_window_class(user32, hwnd), pid.value


def _is_desktop_window(hwnd: int, class_name: str) -> bool:
    if not hwnd or class_name in DESKTOP_WINDOW_CLASSES:
        return True
    if not IS_WINDOWS:
        return False

    user32 = _get_user32()
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    if root in (user32.GetShellWindow(), user32.GetDesktopWindow()):
        return True
    # A desktop icon view can report a child HWND. Check its containing shell window.
    return root != hwnd and _get_window_class(user32, root) in DESKTOP_WINDOW_CLASSES


def wallpaper_covering_window(hwnd: int) -> int | None:
    """Find the desktop surface at the window's center, even when another app has focus."""
    if not IS_WINDOWS or not hwnd:
        return None
    user32 = _get_user32()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    point = wintypes.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    hit = user32.WindowFromPoint(point)
    if not hit or hit == hwnd:
        return None
    root = user32.GetAncestor(hit, GA_ROOT) or hit
    shell = user32.GetShellWindow()
    if root == user32.GetDesktopWindow():
        return shell
    if root == shell or _get_window_class(user32, root) in WALLPAPER_WINDOW_CLASSES:
        return root
    return None


class DesktopFocusMonitor(QObject):
    """Uses EVENT_SYSTEM_FOREGROUND + fallback poll to reveal ZYLO on desktop/Win+D."""

    desktop_focus_changed = pyqtSignal(bool)
    _hook_triggered = pyqtSignal()

    def __init__(self, desklet_hwnd_provider, parent=None):
        super().__init__(parent)
        self._hwnd_provider = desklet_hwnd_provider
        self._pid = os.getpid()
        self._paused = False
        self._hook_handles: list[int] = []
        self._win_event_proc = None

        self._hook_triggered.connect(self._evaluate_foreground)

        # Lightweight safety poll alongside the instant WinEventHook.
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._evaluate_foreground)

    def start(self) -> None:
        self._install_win_event_hooks()
        self._evaluate_foreground()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._uninstall_win_event_hooks()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._evaluate_foreground()

    def _install_win_event_hooks(self) -> None:
        if not IS_WINDOWS or self._hook_handles:
            return
        try:
            user32 = _get_user32()

            def _callback(_hook, _event, _hwnd, _id_obj, _id_child, _thread, _time):
                self._hook_triggered.emit()

            self._win_event_proc = WINEVENTPROC(_callback)
            for event_id in (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_MINIMIZEEND):
                hook = user32.SetWinEventHook(
                    event_id,
                    event_id,
                    None,
                    self._win_event_proc,
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT,
                )
                if hook:
                    self._hook_handles.append(int(hook))
        except Exception:
            self._hook_handles = []

    def _uninstall_win_event_hooks(self) -> None:
        if not IS_WINDOWS or not self._hook_handles:
            return
        try:
            user32 = _get_user32()
            for hook in self._hook_handles:
                user32.UnhookWinEvent(wintypes.HANDLE(hook))
        except Exception:
            pass
        self._hook_handles.clear()
        self._win_event_proc = None

    def _evaluate_foreground(self) -> None:
        if self._paused:
            return
        fg_hwnd, class_name, fg_pid = _get_foreground_info()
        own_hwnd = int(self._hwnd_provider() or 0)
        is_own_process = fg_pid == self._pid or (fg_hwnd != 0 and fg_hwnd == own_hwnd)
        is_desktop = bool(fg_hwnd) and not is_own_process and _is_desktop_window(fg_hwnd, class_name)
        self.desktop_focus_changed.emit(is_desktop)
