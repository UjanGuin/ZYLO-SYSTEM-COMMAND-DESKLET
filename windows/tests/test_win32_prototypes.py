"""Unit tests verifying 64-bit safe Win32 ctypes argtypes and restypes."""

from __future__ import annotations

import ctypes
import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch

from tests.test_support import ensure_runtime_stubs_if_missing

ensure_runtime_stubs_if_missing()

from step_desklet.win11 import dwm, focus_monitor  # noqa: E402


class _FakeFn:
    def __init__(self, name: str):
        self.name = name
        self.argtypes = None
        self.restype = ctypes.c_int  # intentionally start with ctypes default (c_int)


class _FakeDLL:
    def __init__(self, names: list[str]):
        for n in names:
            setattr(self, n, _FakeFn(n))


class Win32PrototypeTests(unittest.TestCase):
    def test_dwm_prototypes_override_default_c_int_returns(self):
        user32 = _FakeDLL(
            [
                "SetWindowCompositionAttribute",
                "SetWindowPos",
                "GetWindowLongPtrW",
                "GetWindowLongW",
                "SetWindowLongPtrW",
                "SetWindowLongW",
            ]
        )
        dwmapi = _FakeDLL(["DwmSetWindowAttribute"])
        kernel32 = _FakeDLL(["CreateMutexW", "CloseHandle", "GetLastError"])

        dwm.configure_win32_prototypes(user32=user32, dwmapi=dwmapi, kernel32=kernel32)

        # Pointer-sized handles must never fall back to 32-bit c_int
        self.assertIs(kernel32.CreateMutexW.restype, wintypes.HANDLE)
        self.assertEqual(
            kernel32.CreateMutexW.argtypes,
            [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR],
        )
        self.assertEqual(kernel32.CloseHandle.argtypes, [wintypes.HANDLE])
        self.assertIs(kernel32.CloseHandle.restype, wintypes.BOOL)

        self.assertIs(dwmapi.DwmSetWindowAttribute.restype, dwm.HRESULT)
        self.assertEqual(
            dwmapi.DwmSetWindowAttribute.argtypes,
            [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD],
        )

        self.assertEqual(
            user32.SetWindowPos.argtypes[:2],
            [wintypes.HWND, wintypes.HWND],
        )
        self.assertIs(user32.SetWindowPos.restype, wintypes.BOOL)
        self.assertIs(user32.SetWindowCompositionAttribute.restype, wintypes.BOOL)
        self.assertIs(user32.GetWindowLongPtrW.restype, dwm.LONG_PTR)
        self.assertIs(user32.SetWindowLongPtrW.restype, dwm.LONG_PTR)

    def test_focus_monitor_prototypes_configure_hwnd_and_hook_handles(self):
        user32 = _FakeDLL(
            [
                "GetForegroundWindow",
                "GetShellWindow",
                "GetDesktopWindow",
                "GetAncestor",
                "GetClassNameW",
                "GetWindowThreadProcessId",
                "SetWinEventHook",
                "UnhookWinEvent",
            ]
        )

        focus_monitor.configure_focus_prototypes(user32)

        self.assertEqual(user32.GetForegroundWindow.argtypes, [])
        self.assertIs(user32.GetForegroundWindow.restype, wintypes.HWND)
        self.assertIs(user32.GetShellWindow.restype, wintypes.HWND)
        self.assertIs(user32.GetDesktopWindow.restype, wintypes.HWND)
        self.assertEqual(user32.GetAncestor.argtypes, [wintypes.HWND, wintypes.UINT])
        self.assertIs(user32.GetAncestor.restype, wintypes.HWND)
        self.assertIs(user32.SetWinEventHook.restype, wintypes.HANDLE)
        self.assertEqual(user32.SetWinEventHook.argtypes[3], focus_monitor.WINEVENTPROC)
        self.assertEqual(user32.UnhookWinEvent.argtypes, [wintypes.HANDLE])
        self.assertIs(user32.UnhookWinEvent.restype, wintypes.BOOL)
        self.assertEqual(
            user32.GetClassNameW.argtypes,
            [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int],
        )

    def test_focus_monitor_reuses_configured_user32(self):
        user32 = _FakeDLL(["GetForegroundWindow"])
        focus_monitor._get_user32.cache_clear()
        self.addCleanup(focus_monitor._get_user32.cache_clear)
        with patch.object(focus_monitor.ctypes, "WinDLL", return_value=user32, create=True) as load_dll:
            self.assertIs(focus_monitor._get_user32(), focus_monitor._get_user32())
            self.assertIs(user32.GetForegroundWindow.restype, wintypes.HWND)
            load_dll.assert_called_once()

    def test_desktop_icon_view_is_classified_by_its_root(self):
        desktop_view = 0x100000001
        wallpaper = 0x100000002
        explorer = 0x100000003
        shell = 0x100000004
        user32 = Mock()
        user32.GetShellWindow.return_value = shell
        user32.GetDesktopWindow.return_value = 0x100000005
        user32.GetAncestor.side_effect = [wallpaper, explorer, shell]
        with (
            patch.object(focus_monitor, "IS_WINDOWS", True),
            patch.object(focus_monitor, "_get_user32", return_value=user32),
            patch.object(focus_monitor, "_get_window_class", side_effect=["WorkerW", "CabinetWClass"]),
        ):
            self.assertTrue(focus_monitor._is_desktop_window(desktop_view, "SHELLDLL_DefView"))
            self.assertFalse(focus_monitor._is_desktop_window(desktop_view, "SHELLDLL_DefView"))
            self.assertTrue(focus_monitor._is_desktop_window(shell, "ShellHost"))

    def test_wallpaper_probe_ignores_the_desklet_and_intervening_apps(self):
        user32 = Mock()
        desklet = 0x100000001
        child = 0x100000002
        wallpaper = 0x100000003
        other_window = 0x100000004
        user32.GetShellWindow.return_value = 0x100000005
        user32.GetDesktopWindow.return_value = 0x100000006
        user32.GetAncestor.side_effect = lambda hwnd, _flag: {child: wallpaper, other_window: other_window}[hwnd]

        def get_rect(_hwnd, pointer):
            rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = -600, 20, -300, 140
            return True

        user32.GetWindowRect.side_effect = get_rect
        user32.WindowFromPoint.side_effect = [desklet, other_window, child]
        with (
            patch.object(focus_monitor, "IS_WINDOWS", True),
            patch.object(focus_monitor, "_get_user32", return_value=user32),
            patch.object(focus_monitor, "_get_window_class", side_effect=["CabinetWClass", "WorkerW"]),
        ):
            self.assertIsNone(focus_monitor.wallpaper_covering_window(desklet))
            self.assertIsNone(focus_monitor.wallpaper_covering_window(desklet))
            self.assertEqual(focus_monitor.wallpaper_covering_window(desklet), wallpaper)
        point = user32.WindowFromPoint.call_args.args[0]
        self.assertEqual((point.x, point.y), (-450, 80))

    def test_native_restore_preserves_window_order_and_activation(self):
        user32 = Mock()
        user32.IsWindow.return_value = True
        user32.GetWindow.side_effect = [300, 301, 100]
        user32.GetWindowLongPtrW.side_effect = [0, dwm.WS_EX_TOPMOST]
        flags = dwm.SWP_NOMOVE | dwm.SWP_NOSIZE | dwm.SWP_NOACTIVATE | dwm.SWP_SHOWWINDOW
        with patch.object(dwm, "_get_win32_libs", return_value=(user32, None, None)):
            dwm.show_window_above_wallpaper(100, 200)
            user32.SetWindowPos.assert_called_with(100, 300, 0, 0, 0, 0, flags)
            dwm.show_window_above_wallpaper(100, 200)
            user32.SetWindowPos.assert_called_with(100, dwm.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            dwm.show_window_above_wallpaper(100, 200)
            user32.SetWindowPos.assert_called_with(100, None, 0, 0, 0, 0, flags | dwm.SWP_NOZORDER)


if __name__ == "__main__":
    unittest.main()
