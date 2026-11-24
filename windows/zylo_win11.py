#!/usr/bin/env python3
"""ZYLO Desktop Assistant entrypoint for Windows 11 26H2."""

import argparse
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

# Ensure project root is importable when launched from source or frozen PyInstaller bundle.
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from step_desklet.win11.branding import set_zylo_app_user_model_id, zylo_app_icon
from step_desklet.win11.dwm import (
    acquire_single_instance_mutex,
    close_win32_handle,
    open_settings_request_event,
    release_single_instance_mutex,
    signal_settings_request,
    take_settings_request,
)
from step_desklet.win11.request_process import run_launch_worker, run_request_worker
from step_desklet.win11.tray_app import ZyloWin11Controller


def listen_for_settings(handle, controller: ZyloWin11Controller) -> QTimer:
    def open_settings() -> None:
        if take_settings_request(handle):
            controller.open_setup()

    timer = QTimer(controller.app)
    timer.timeout.connect(open_settings)
    timer.start(200)
    return timer


def main() -> int:
    if sys.argv[1:] == ["--request-worker"]:
        return run_request_worker()
    if sys.argv[1:] == ["--launch-item"]:
        return run_launch_worker()

    parser = argparse.ArgumentParser(description="ZYLO Desktop Assistant for Windows 11 26H2")
    launch = parser.add_mutually_exclusive_group()
    launch.add_argument("--setup", action="store_true", help="Open ZYLO Settings (default)")
    launch.add_argument("--background", action="store_true", help="Start the desklet at Windows sign-in")
    args, qt_args = parser.parse_known_args()

    # Improve crisp PerMonitorV2 rendering on Windows 11 26H2 high-DPI displays.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    set_zylo_app_user_model_id()
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("ZYLO")
    app.setApplicationDisplayName("ZYLO Desktop Assistant")
    app.setOrganizationName("ZYLO")
    app.setWindowIcon(zylo_app_icon())

    settings_event = None
    mutex_handle = None
    controller = None
    settings_timer = None
    try:
        settings_event = open_settings_request_event()
        mutex_handle = acquire_single_instance_mutex()
        if mutex_handle is None:
            if not args.background:
                signal_settings_request(settings_event)
            return 0

        controller = ZyloWin11Controller(app)
        settings_timer = listen_for_settings(settings_event, controller)
        controller.start(force_setup=not args.background)
        return app.exec()
    except Exception as error:
        QMessageBox.critical(None, "ZYLO could not start", str(error))
        return 1
    finally:
        if settings_timer is not None:
            settings_timer.stop()
        if controller is not None:
            controller.shutdown_cleanup()
        release_single_instance_mutex(mutex_handle)
        close_win32_handle(settings_event)


if __name__ == "__main__":
    raise SystemExit(main())
