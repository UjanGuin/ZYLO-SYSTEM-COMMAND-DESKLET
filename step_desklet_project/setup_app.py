#!/usr/bin/env python3
"""ZYLO first-run and settings launcher."""

import json
import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)


CONFIG_DIR = Path.home() / ".config" / "step-desklet"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
ENV_FILE = Path.home() / ".config" / "step-desklet.env"
API_KEY_URL = "https://build.nvidia.com/"
GROQ_API_KEY_URL = "https://console.groq.com/keys"
TAVILY_API_KEY_URL = "https://app.tavily.com/home"
ACCENTS = {
    "Dark Violet": "#a78bfa",
    "Violet": "#b39aff",
    "Mint": "#75e0bd",
    "Rose": "#ff9eb5",
    "Amber": "#ffd27a",
}


class SetupDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Set up ZYLO")
        self.setMinimumWidth(430)
        self.setStyleSheet("""
            QDialog { background: #172336; color: #f4f8ff; }
            QLabel { color: #e7f0ff; }
            QLineEdit { color: white; background: rgba(255,255,255,18); border: 1px solid rgba(220,238,255,65); border-radius: 8px; padding: 9px; }
            QPushButton { color: #f6f9ff; background: rgba(255,255,255,18); border: 1px solid rgba(220,238,255,55); border-radius: 8px; padding: 8px 12px; }
            QPushButton:hover { background: rgba(255,255,255,32); }
        """)
        try:
            current = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            current = {}
        self.is_activated = bool(current.get("name") and ENV_FILE.exists())
        self.name = QLineEdit(current.get("name", ""))
        self.name.setPlaceholderText("What should ZYLO call you?")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("nvapi-…")
        self.key.setToolTip("Leave blank to keep the saved key")
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_key.setPlaceholderText("gsk_…")
        self.groq_key.setToolTip("Leave blank to keep the saved key")
        self.tavily_key = QLineEdit()
        self.tavily_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tavily_key.setPlaceholderText("tvly-…")
        self.tavily_key.setToolTip("Leave blank to keep the saved key")
        self.accent = current.get("accent", ACCENTS["Dark Violet"])
        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(self._choose_accent)
        self._show_accent()

        title = QLabel("Welcome to ZYLO")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: white")
        description = QLabel("Personalize your desktop assistant. Your API key stays on this laptop.")
        description.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Your name", self.name)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        get_key = QPushButton("Get NVIDIA key")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_KEY_URL)))
        key_row.addWidget(get_key)
        form.addRow("NVIDIA API key", key_row)
        groq_row = QHBoxLayout()
        groq_row.addWidget(self.groq_key, 1)
        get_groq_key = QPushButton("Get Groq key")
        get_groq_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GROQ_API_KEY_URL)))
        groq_row.addWidget(get_groq_key)
        form.addRow("Groq API key", groq_row)
        tavily_row = QHBoxLayout()
        tavily_row.addWidget(self.tavily_key, 1)
        get_tavily_key = QPushButton("Get Tavily key")
        get_tavily_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(TAVILY_API_KEY_URL)))
        tavily_row.addWidget(get_tavily_key)
        form.addRow("Tavily search key", tavily_row)
        form.addRow("Accent color", self.accent_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        if self.is_activated:
            activated = QLabel("ZYLO is already activated on this laptop. You can update your settings below.")
            activated.setWordWrap(True)
            activated.setStyleSheet("color: #75e0bd; font-weight: 600; padding: 6px 0")
            layout.addWidget(activated)
        layout.addWidget(description)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _choose_accent(self):
        color = QColorDialog.getColor(parent=self, title="Choose ZYLO accent")
        if color.isValid():
            self.accent = color.name()
            self._show_accent()

    def _show_accent(self):
        self.accent_button.setText(self.accent)
        self.accent_button.setStyleSheet(f"background: {self.accent}; color: #142235; font-weight: 700; border-radius: 8px; padding: 8px")

    def values(self):
        return {"name": self.name.text().strip(), "accent": self.accent}


def install_extension():
    subprocess.run(["gnome-extensions", "enable", "zylo-command@local"], check=True, capture_output=True, text=True)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ZYLO")
    dialog = SetupDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return 0
    values = dialog.values()
    if not values["name"]:
        QMessageBox.warning(dialog, "Name required", "Enter your name to continue.")
        return main()

    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        previous_key = ""
        previous_groq_key = ""
        previous_tavily_key = ""
        env_lines = []
        if ENV_FILE.exists():
            env_lines = ENV_FILE.read_text().splitlines()
            for line in env_lines:
                if line.startswith(("NVIDIA_API_KEY=", "NIM_API_KEY=")):
                    previous_key = line.split("=", 1)[1].strip().strip("\"'")
                elif line.startswith("GROQ_API_KEY="):
                    previous_groq_key = line.split("=", 1)[1].strip().strip("\"'")
                elif line.startswith("TAVILY_API_KEY="):
                    previous_tavily_key = line.split("=", 1)[1].strip().strip("\"'")
        api_key = dialog.key.text().strip() or previous_key
        if not api_key:
            QMessageBox.warning(dialog, "API key required", "Enter a key or use Get NVIDIA key to create one.")
            return main()
        SETTINGS_FILE.write_text(json.dumps(values, indent=2) + "\n")
        SETTINGS_FILE.chmod(0o600)
        env_written = False
        for index, line in enumerate(env_lines):
            if line.startswith("NVIDIA_API_KEY="):
                env_lines[index] = f"NVIDIA_API_KEY={api_key}"
                env_written = True
                break
        if not env_written:
            env_lines.append(f"NVIDIA_API_KEY={api_key}")
        groq_api_key = dialog.groq_key.text().strip() or previous_groq_key
        if groq_api_key:
            groq_written = False
            for index, line in enumerate(env_lines):
                if line.startswith("GROQ_API_KEY="):
                    env_lines[index] = f"GROQ_API_KEY={groq_api_key}"
                    groq_written = True
                    break
            if not groq_written:
                env_lines.append(f"GROQ_API_KEY={groq_api_key}")
        tavily_api_key = dialog.tavily_key.text().strip() or previous_tavily_key
        if tavily_api_key:
            tavily_written = False
            for index, line in enumerate(env_lines):
                if line.startswith("TAVILY_API_KEY="):
                    env_lines[index] = f"TAVILY_API_KEY={tavily_api_key}"
                    tavily_written = True
                    break
            if not tavily_written:
                env_lines.append(f"TAVILY_API_KEY={tavily_api_key}")
        ENV_FILE.write_text("\n".join(env_lines).rstrip() + "\n")
        ENV_FILE.chmod(0o600)
        install_extension()
    except Exception as error:
        QMessageBox.critical(dialog, "ZYLO setup failed", str(error))
        return 1
    QMessageBox.information(dialog, "ZYLO is ready", "ZYLO is enabled on your desktop and will start with GNOME when you log in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
