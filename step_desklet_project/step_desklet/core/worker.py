import json
import os
import subprocess
import threading
import time
from typing import Optional, Dict, List, Any
from PyQt6.QtCore import QObject, pyqtSignal
from openai import OpenAI

SYSTEM_PROMPT = """You are the ZYLO-SYSTEM-COMMAND-DESKLET Laptop Intelligence.
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
            proc = subprocess.run(
                cmd, 
                shell=True, 
                text=True, 
                capture_output=True, 
                timeout=120, 
                input=sudo_password + "\n" if "sudo -S" in cmd else None
            )
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    def _build_feedback(self, command: str, result: Dict[str, Any]) -> str:
        return f"cmd: {command}\nexit: {result['exit_code']}\nout: {result['stdout']}\nerr: {result['stderr']}"
