#!/usr/bin/env python3
import sys
import os
import argparse
from PyQt6.QtWidgets import QApplication
from step_desklet.ui.manager import DeskletManager
from step_desklet.system.autostart import setup_autostart
from step_desklet.core.config import save_configs, DEFAULT_CONFIG


def parse_args():
    parser = argparse.ArgumentParser(description="ZYLO-SYSTEM-COMMAND-DESKLET Manager")
    parser.add_argument("--new", action="store_true", help="Create a new desklet instance")
    parser.add_argument("--reset", action="store_true", help="Reset all configurations")
    parser.add_argument("--no-autostart", action="store_true", help="Disable autostart setup")
    return parser.parse_args()


def main():
    # Add the current directory to sys.path
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    args = parse_args()

    if args.reset:
        print("Resetting all configurations...")
        save_configs(DEFAULT_CONFIG)

    # Setup autostart by default using launcher script when present.
    if not args.no_autostart:
        launcher_path = os.path.join(project_root, "step-desklet")
        app_abs_path = launcher_path if os.path.isfile(launcher_path) else os.path.abspath(__file__)
        setup_autostart(app_abs_path)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    manager = DeskletManager()
    manager.start()

    if args.new:
        manager.create_desklet()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

