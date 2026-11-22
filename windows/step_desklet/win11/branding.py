"""ZYLO's window icon and Windows shell identity."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtGui import QIcon


ZYLO_APP_USER_MODEL_ID = "ZYLO.DesktopAssistant"


def zylo_app_icon() -> QIcon:
    asset = Path(__file__).resolve().parents[2] / "packaging" / "zylo.ico"
    return QIcon(str(asset))


def set_zylo_app_user_model_id() -> bool:
    if sys.platform != "win32":
        return False
    set_app_id = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = [wintypes.LPCWSTR]
    set_app_id.restype = ctypes.c_long
    return set_app_id(ZYLO_APP_USER_MODEL_ID) == 0
