"""Open known Windows administration tools through User Account Control."""

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from step_desklet.core.admin_tools import ADMIN_TOOLS


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
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def launch_admin_tool(tool_id: str, parent_handle: int) -> bool:
    """Launch one built-in tool. Return False when Windows consent is cancelled."""
    if sys.platform != "win32":
        raise RuntimeError("Administrator tools are available on Windows only.")
    tool = ADMIN_TOOLS[tool_id]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    system_directory = kernel.GetSystemDirectoryW
    system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    system_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    count = system_directory(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise OSError("Windows could not resolve its system directory.")

    directory = Path(buffer.value)
    executable = directory / tool.executable
    console = directory / tool.console if tool.console else None
    if not executable.is_file() or (console is not None and not console.is_file()):
        raise OSError(f"Windows could not find {tool.label}.")

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    execute.restype = wintypes.BOOL
    request = ShellExecuteInfo()
    request.cbSize = ctypes.sizeof(request)
    request.fMask = 0x00000100 | 0x00000400  # SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI
    request.hwnd = parent_handle
    request.lpVerb = "runas"
    request.lpFile = str(executable)
    request.lpParameters = subprocess.list2cmdline([str(console)]) if console is not None else None
    request.lpDirectory = str(directory)
    request.nShow = 1  # SW_SHOWNORMAL

    if execute(ctypes.byref(request)):
        return True
    error = ctypes.get_last_error()
    if error == 1223:  # ERROR_CANCELLED
        return False
    raise ctypes.WinError(error)
