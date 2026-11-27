#!/usr/bin/env python3
"""liNi Desktop Assistant entrypoint for Windows 11 26H2."""

import argparse
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, Qt, QTimer
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


def listen_for_settings(handle, controller: ZyloWin11Controller, chats_handle=None) -> QTimer:
    def open_settings() -> None:
        if take_settings_request(handle):
            controller.open_setup()
        if chats_handle and take_settings_request(chats_handle):
            controller.open_chats()

    timer = QTimer(controller.app)
    timer.timeout.connect(open_settings)
    timer.start(200)
    return timer


def main() -> int:
    if sys.argv[1:] == ["--request-worker"]:
        return run_request_worker()
    if sys.argv[1:] == ["--launch-item"]:
        return run_launch_worker()

    parser = argparse.ArgumentParser(description="liNi Desktop Assistant for Windows 11 26H2")
    launch = parser.add_mutually_exclusive_group()
    launch.add_argument("--setup", action="store_true", help="Open liNi Settings (default)")
    launch.add_argument("--background", action="store_true", help="Start the desklet at Windows sign-in")
    launch.add_argument("--chats", action="store_true", help="Open liNi Chats")
    args, qt_args = parser.parse_known_args()

    # Improve crisp PerMonitorV2 rendering on Windows 11 26H2 high-DPI displays.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    set_zylo_app_user_model_id()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("liNi")
    app.setApplicationDisplayName("liNi Desktop Assistant")
    app.setOrganizationName("ZYLO")
    app.setWindowIcon(zylo_app_icon())

    settings_event = None
    chats_event = None
    mutex_handle = None
    controller = None
    settings_timer = None
    try:
        settings_event = open_settings_request_event()
        chats_event = open_settings_request_event("Local\\ZYLODesktopAssistantChats")
        mutex_handle = acquire_single_instance_mutex()
        if mutex_handle is None:
            if args.chats:
                signal_settings_request(chats_event)
            elif not args.background:
                signal_settings_request(settings_event)
            return 0

        controller = ZyloWin11Controller(app)
        settings_timer = listen_for_settings(settings_event, controller, chats_event)
        controller.start(force_setup=not (args.background or args.chats), open_chats=args.chats)
        return app.exec()
    except Exception as error:
        QMessageBox.critical(None, "liNi could not start", str(error))
        return 1
    finally:
        if settings_timer is not None:
            settings_timer.stop()
        if controller is not None:
            controller.shutdown_cleanup()
        release_single_instance_mutex(mutex_handle)
        close_win32_handle(settings_event)
        close_win32_handle(chats_event)


if __name__ == "__main__":
    raise SystemExit(main())
