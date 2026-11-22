"""Shared test bootstrap adding step_desklet_project to sys.path and optional headless stubs."""

from __future__ import annotations

import sys
import types
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def ensure_runtime_stubs_if_missing() -> None:
    """Install lightweight stubs only when PyQt6/requests are unavailable (e.g. minimal CI/container)."""
    if "PyQt6" not in sys.modules:
        try:
            import PyQt6  # noqa: F401
        except ImportError:
            pyqt6 = types.ModuleType("PyQt6")
            qtcore = types.ModuleType("PyQt6.QtCore")

            class _BoundSignal:
                def __init__(self):
                    self._slots = []

                def connect(self, fn):
                    self._slots.append(fn)

                def emit(self, *args, **kwargs):
                    for fn in list(self._slots):
                        fn(*args, **kwargs)

            class _SignalDescriptor:
                def __init__(self, *_args):
                    self._attr = None

                def __set_name__(self, owner, name):
                    self._attr = f"__sig_{name}"

                def __get__(self, instance, owner):
                    if instance is None:
                        return self
                    sig = instance.__dict__.get(self._attr)
                    if sig is None:
                        sig = _BoundSignal()
                        instance.__dict__[self._attr] = sig
                    return sig

            class _QObject:
                def __init__(self, *_args, **_kwargs):
                    pass

            class _QTimer:
                def __init__(self, *_args, **_kwargs):
                    self.timeout = _BoundSignal()

                def setInterval(self, *_args):
                    pass

                def start(self, *_args):
                    pass

                def stop(self):
                    pass

            qtcore.QObject = _QObject
            qtcore.QTimer = _QTimer
            qtcore.pyqtSignal = lambda *args: _SignalDescriptor(*args)
            qtcore.pyqtSlot = lambda *_args, **_kwargs: lambda method: method
            pyqt6.QtCore = qtcore
            sys.modules["PyQt6"] = pyqt6
            sys.modules["PyQt6.QtCore"] = qtcore

    if "requests" not in sys.modules:
        try:
            import requests  # noqa: F401
        except ImportError:
            req = types.ModuleType("requests")

            class _RequestException(Exception):
                pass

            class _Session:
                def post(self, *args, **kwargs):
                    raise _RequestException("stubbed requests.Session")

                def close(self):
                    pass

            req.RequestException = _RequestException
            req.Response = object
            req.Session = _Session
            req.post = lambda *args, **kwargs: None
            req.get = lambda *args, **kwargs: None
            sys.modules["requests"] = req
