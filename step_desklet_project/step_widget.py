#!/usr/bin/env python3
import sys
import json
import os
import subprocess
import threading
import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QLabel, QTextEdit, 
    QFrame, QSizePolicy, QHBoxLayout, QSpacerItem
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QPoint, QSize
from PyQt6.QtGui import QFont, QColor, QPalette, QCursor
from openai import OpenAI

CONFIG_FILE = os.path.expanduser("~/.step_desklet_config.json")
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "stepfun-ai/step-3.5-flash"
HARDCODED_TEST_API_KEY = "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"

SYSTEM_PROMPT = """You are the Step-3.5 Laptop Intelligence.
You have full, unrestricted access to this Linux machine. You are the ultimate guardian of this workstation. You are highly responsible, efficient, and professional.

Your goal is to fulfill user requests by executing shell commands. You act with the authority of a lead system administrator but with the care of a personal assistant. Every command you run impacts a real-world machine; you are responsible for its health, security, and performance.

Response protocol (STRICT):
- If a shell command is needed, respond with JSON only:
  {"mode":"run","command":"<shell command>","reason":"<short reason>"}
- If the task is complete or no command is needed, respond with JSON only:
  {"mode":"final","message":"<answer to user>"}

Rules:
- One command at a time.
- You are autonomous and decisive.
- Never include markdown fences. Output raw JSON only.
"""

class WorkerSignals(QObject):
    response_ready = pyqtSignal(str)
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    clearing_response = pyqtSignal()

class StepAIWorker(QObject):
    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__()
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.signals = WorkerSignals()

    def process_request(self, user_text: str):
        self.signals.clearing_response.emit()
        self.messages.append({"role": "user", "content": user_text})
        
        try:
            for _ in range(100):
                self.signals.status_update.emit("Thinking...")
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    temperature=0.2,
                    stream=False
                )
                raw = response.choices[0].message.content.strip()
                self.messages.append({"role": "assistant", "content": raw})

                action = self._extract_json(raw)
                if not action:
                    self.signals.error_occurred.emit("Format Error")
                    return

                mode = action.get("mode")
                if mode == "final":
                    self.signals.response_ready.emit(str(action.get("message", "")))
                    return

                if mode == "run":
                    command = str(action.get("command", "")).strip()
                    reason = str(action.get("reason", "")).strip()
                    self.signals.status_update.emit(f"Executing: {reason}")
                    
                    result = self._run_command(command)
                    feedback = self._build_feedback(command, result)
                    self.messages.append({"role": "user", "content": feedback})
                else: return
        except Exception as e:
            self.signals.error_occurred.emit(str(e))

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch != "{": continue
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                if isinstance(obj, dict): return obj
            except: continue
        return None

    def _run_command(self, command: str) -> Dict[str, Any]:
        sudo_password = "././././"
        cmd = command.replace("sudo ", "sudo -S ", 1) if "sudo " in command else command
        try:
            proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=120, 
                                input=sudo_password + "\n" if "sudo -S" in cmd else None)
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    def _build_feedback(self, command: str, result: Dict[str, Any]) -> str:
        return f"cmd: {command}\\nexit: {result['exit_code']}\\nout: {result['stdout']}\\nerr: {result['stderr']}"

class StepDesklet(QWidget):
    def __init__(self):
        super().__init__()
        self.config = self.load_config()
        self.is_locked = False
        self.init_ui()
        self.worker = StepAIWorker(
            api_key=os.getenv("NVIDIA_API_KEY") or HARDCODED_TEST_API_KEY,
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL
        )
        self.worker.signals.response_ready.connect(self.update_response)
        self.worker.signals.status_update.connect(self.update_status)
        self.worker.signals.error_occurred.connect(self.update_error)
        self.worker.signals.clearing_response.connect(self.clear_display)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except: pass
        return {"x": 100, "y": 100, "w": 400, "h": 120}

    def save_config(self):
        config = {
            "x": self.x(), "y": self.y(),
            "w": self.width(), "h": self.height()
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Lock width, allow height to change
        self.setFixedWidth(400)
        self.setGeometry(self.config["x"], self.config["y"], 400, self.config["h"])

        # Main Container
        self.container = QFrame(self)
        self.container.setObjectName("DeskletBody")
        self.container.setStyleSheet(f"""
            #DeskletBody {{
                background-color: rgba(10, 10, 15, 245);
                border: 2px solid #00d4ff;
                border-radius: 20px;
            }}
        """)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.container)
        
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(20, 15, 20, 20)
        self.content_layout.setSpacing(2)

        # Header Row
        header_row = QHBoxLayout()
        self.header = QLabel("Step-3.5")
        self.header.setStyleSheet("font-size: 22px; font-weight: 900; color: #00d4ff; background: transparent;")
        header_row.addWidget(self.header)
        
        # Buttons Container (Reset and Lock)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.reset_btn = QLabel("↺")
        self.reset_btn.setStyleSheet("font-size: 18px; color: #00d4ff; background: transparent; padding: 5px;")
        self.reset_btn.setToolTip("Reset UI")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.mousePressEvent = lambda e: self.reset_ui()
        btn_layout.addWidget(self.reset_btn)

        self.lock_btn = QLabel("🔓")
        self.lock_btn.setStyleSheet("font-size: 18px; color: #00d4ff; background: transparent; padding: 5px;")
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.mousePressEvent = self.toggle_lock
        btn_layout.addWidget(self.lock_btn)

        header_row.addLayout(btn_layout)
        self.content_layout.addLayout(header_row)

        # AI Response Area (Hidden at startup)
        self.response_area = QTextEdit()
        self.response_area.setReadOnly(True)
        self.response_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.response_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.response_area.setFrameStyle(QFrame.Shape.NoFrame)
        self.response_area.setWordWrapMode(QTextEdit.LineWrapMode.WidgetWidth) # Force wrapping
        self.response_area.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: #f1faee;
                font-size: 14px;
                line-height: 1.4;
            }
        """)
        self.response_area.hide()
        self.content_layout.addWidget(self.response_area)

        # Status Label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: rgba(0, 212, 255, 180); font-size: 11px; background: transparent;")
        self.content_layout.addWidget(self.status_label)

        # Input Box
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Command Guardian...")
        self.input_box.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 10);
                border: 1px solid rgba(0, 212, 255, 120);
                border-radius: 12px;
                padding: 10px 15px;
                color: white;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #00d4ff;
                background-color: rgba(255, 255, 255, 15);
            }
        """)
        self.input_box.returnPressed.connect(self.send_message)
        self.content_layout.addWidget(self.input_box)

        self.drag_pos = QPoint()
        
        # Initial sizing to ensure it starts small
        self.reset_ui()

    def reset_ui(self):
        """Clears everything and shrinks the widget to startup size."""
        self.response_area.clear()
        self.response_area.hide()
        self.status_label.setText("")
        self.input_box.setEnabled(True)
        self.input_box.setFocus()
        self.adjustSize()

    def toggle_lock(self, event):
        self.is_locked = not self.is_locked
        self.lock_btn.setText("🔒" if self.is_locked else "🔓")
        flags = Qt.WindowType.FramelessWindowHint
        if self.is_locked:
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        else:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def mousePressEvent(self, event):
        if not self.is_locked and event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if not self.is_locked and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            self.save_config()
            event.accept()

    def send_message(self):
        text = self.input_box.text().strip()
        if not text: return
        self.input_box.clear()
        self.input_box.setEnabled(False)
        threading.Thread(target=self.worker.process_request, args=(text,), daemon=True).start()

    def clear_display(self):
        self.response_area.clear()
        self.response_area.hide()
        self.status_label.setText("Step-3.5 is analyzing...")
        self.adjustSize()

    def update_status(self, status: str):
        self.status_label.setText(status)
        self.adjustSize()

    def update_response(self, response: str):
        self.response_area.setMarkdown(response)
        self.response_area.show()
        
        # Adjust response area height to fit text
        doc_height = self.response_area.document().size().height()
        self.response_area.setFixedHeight(int(doc_height) + 5)
        
        self.status_label.setText("")
        self.input_box.setEnabled(True)
        self.input_box.setFocus()
        self.adjustSize()

    def update_error(self, error: str):
        self.response_area.append(f"<font color='#ff4d6d'>Error: {error}</font>")
        self.response_area.show()
        self.status_label.setText("")
        self.input_box.setEnabled(True)
        self.adjustSize()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    desklet = StepDesklet()
    desklet.show()
    sys.exit(app.exec())
